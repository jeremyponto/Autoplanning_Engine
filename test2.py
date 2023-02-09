import json
import numpy
from datetime import datetime
from datetime import timedelta
from itertools import groupby
from operator import itemgetter
import uuid

f = open('new_sample_data_update.json')
data = json.load(f)
data = data['order_recap']['assy_mirror_4w']

def adjust_breaktime(session, starttime, duration, dandory_time):
	starttime = starttime + timedelta(minutes=dandory_time)
	endtime = starttime + timedelta(minutes=duration)

	n_breaktime = len(session['break_time_start'])
	for idx in range(n_breaktime):

		start_break = datetime.strptime(starttime.strftime('%m/%d/%Y') + " " + session['break_time_start'][idx], '%m/%d/%Y %H:%M:%S')
		end_break = datetime.strptime(endtime.strftime('%m/%d/%Y') + " " + session['break_time_end'][idx], '%m/%d/%Y %H:%M:%S')

		if start_break <= starttime and starttime <= end_break:
			starttime = end_break + timedelta(minutes=dandory_time)
			endtime = starttime + timedelta(minutes=duration)
			break
		
		if start_break < endtime and endtime < end_break:
			time_difference = (endtime - start_break).seconds/60
			endtime = end_break + timedelta(minutes=time_difference)
			break

	return starttime, endtime

def init_sessions(data,start_date,max_overtime):
	"""
	# initial Shift / Session data
	Returns :
	{
		"session_id" : str
		"start_shift" : HH:MM:SS
		"end_shift" : HH:MM:SS
		"overtime_start" : HH:MM:SS
		"overtime_end" : HH:MM:SS
		"break_time_start" :
		[
			HH:MM:SS
		]
		"break_time_end" :
		[
			HH:MM:SS
		]
		"break_time_duration" :
		[
			HH:MM:SS
		]
		"work_duration" : int #additional
		"max_overtime" : int #additional
	}
	"""
	m_sessions = []
	for shift in data['shift_time']['shift_list']:
		temp = data['shift_time'][shift['name']]
		temp['max_overtime'] = max_overtime
		
		start_time = datetime.strptime(temp['start_shift'],"%H:%M:%S")
		end_time = datetime.strptime(temp['end_shift'],"%H:%M:%S")

		temp['work_duration'] = ((end_time - start_time).seconds/60) - sum(temp['break_time_duration'])

		temp['start_datetime'] = datetime.strptime(start_date + " " + temp['start_shift'], '%m/%d/%Y %H:%M:%S')

		if start_time > end_time:
			temp['end_datetime'] = datetime.strptime(start_date + " " +temp['end_shift'], '%m/%d/%Y %H:%M:%S') + timedelta(days=1)
		else:
			temp['end_datetime'] = datetime.strptime(start_date + " " +temp['end_shift'], '%m/%d/%Y %H:%M:%S')
		
		temp['session_id'] = shift['name']
		m_sessions.append(temp)
	return m_sessions

def init_orders(data):
	"""
	# initial Master Order data
	Returns :
	{
		"key",
		"part_number"
		"qty" : int
		"production_deadline"
		"ship_to_party"
		"sold_to_party"
		"duration_time" : [
			"duration" : int
			"line"
			"line_id"
		]
		"duedate" : date format %m/%d/%Y/%H:%M  #Additional
	}
	"""
	m_orders = data['order']
	m_orders = [dict(order, **{'duedate':datetime.strptime(order['production_deadline'], '%m/%d/%Y/%H:%M')}) for order in m_orders]

	new_m_orders = []
	# Grouping Order by part nmber and production_deadline
	for (part_number, production_deadline), orders in groupby(m_orders, key = itemgetter('part_number','production_deadline')):
		orders = list(orders)

		key = []
		qty = 0
		lst_dur = []
		
		for order in orders:
			lst_dur.append([prefer_line['duration'] for prefer_line in order['duration_time']])
			qty += order['qty']
			key.append(order['key'])

		line_dur = [sum(list(dur)) for dur in zip(*lst_dur)]
		temp = {
			'key': key,
			'part_number': part_number,
			'qty': qty,
			'production_deadline': production_deadline,
			'ship_to_party': orders[0]['ship_to_party'],
			'sold_to_party': orders[0]['sold_to_party'],
			'duedate': orders[0]['duedate'],
			'type_product' : orders[0]['type_product'],
			'duration_time': [{'duration': line_dur[i], 'line': orders[0]['duration_time'][i]['line'], 'line_id': orders[0]['duration_time'][i]['line_id']} for i in range(len(orders[0]['duration_time']))]
		}

		new_m_orders.append(temp)

	new_m_orders.sort(key = lambda x: (x['duedate'], x['type_product'], -x['qty'] ))

	return new_m_orders

def init_materials(data):
	"""
	# initial Master Materials data
	Returns :
	{
		"current_stock" : int
		"max_stock" : int
		"min_stock" : int
		"part_number"
		"production_version"
		"prefered_line" : [
			__line name__ : sph
		]
		"material" : [
			"MCode"
			"Mqty"
		]
	}
	"""

	m_materials = data['material_information']
	m_materials = [{**material,**{'diff':material['current_stock'] - material['min_stock'], 'threshold': (material['max_stock'] + material['min_stock'])/2}} for material in m_materials]

	m_materials.sort(key = lambda x: (x['diff'], -x['threshold'])) # (-) means descending

	return m_materials

def init_lines(data):
	"""
	# initial Master Lines data
	Returns :
	[
		__line name__
	]
	"""

	return data['lines']

def autoplanning(data):
	# Configuration Variables
	min_overtime = 60 # in minutes
	max_overtime = 120 # in minutes
	# min_unit_prod = 4 # number of unit
	dandory_time = 5 # in minutes
	line_overload = set()

	start_date = data['start_date'] # format date => %m/%d/%Y
	m_sessions = init_sessions(data,start_date,max_overtime)
	m_orders = init_orders(data)
	m_materials = init_materials(data)
	m_lines = init_lines(data)
	vproduction_plan = {session['session_id']: {line['line']:{'schedule': [], 'total_dur': 0, 'overtime': 0, "line_id":line['line_id']} for line in m_lines} for session in m_sessions}
	sessionxline = len(m_sessions)*len(m_lines) # numb session x line
	# print("len msession :", len(m_sessions), " len mlines :", len(m_lines))

	m_sessions = {session['session_id']:session for session in m_sessions}
	plan_failed = []

	# print(vproduction_plan)
	# print(m_sessions)
	# print('======== shift')
	# print("data Loaded")
	# Make to Order

	for order in m_orders:
		order_occupied = False
		order_failed = False
		matt= [(d['production_version'],d['material'],d['minimum_unit_production']) for d in m_materials if d['part_number'] == order['part_number']]
		production_version = matt[0][0]
		materials_ = matt[0][1]
		min_unit_prod = matt[0][2]

		if min_unit_prod == 0:
			min_unit_prod = 1

		for session_id, plan in vproduction_plan.items():
			for line_dur in order['duration_time']:
				try:
					plan_schedule = plan[line_dur['line']]['schedule']
					plan_totaldur = plan[line_dur['line']]['total_dur']
				except:
					continue

				# Initial plan production (first order insert)
				if not plan_schedule:
					if order['duedate'] >= m_sessions[session_id]['start_datetime'] and plan_totaldur + line_dur['duration'] <= m_sessions[session_id]['work_duration']:
						# adjust_breaktime -> (session, starttime, duration, dandory_time)
						starttime, endtime = adjust_breaktime(m_sessions[session_id], m_sessions[session_id]['start_datetime'], line_dur['duration'], dandory_time)
						temp = {
							'key':order['key'],
							'line':line_dur['line'],
							'part_number': order['part_number'],
							'qty': order['qty'],
							'production_time_minutes': line_dur['duration'],
							'start_datetime': m_sessions[session_id]['start_datetime'],
							'end_datetime': (m_sessions[session_id]['start_datetime'] + timedelta(minutes=line_dur['duration'])),
							'duedate': order['duedate'],
							"ship_to_party" : order['ship_to_party'],
							"sold_to_party" : order['sold_to_party'],
							"production_version": production_version,
							"type_product" : order['type_product'],
							"material_needs" : [{"MCode":material['MCode'],"MName":material['MName'],"Mqty":int(material['Mqty'])*order["qty"]} for material in materials_],
						}

						plan_schedule.append(temp)
						plan[line_dur['line']]['total_dur'] = plan_totaldur + line_dur['duration']+dandory_time
						order_occupied = True

					else:
						order_failed = True
						order_temp = order.copy()
						order_temp['failed_reason'] = "duedate overdue"
						plan_failed.append(order_temp)
					
					break

				# Delta plan production (seconds order insert)
				else:
					# if duedate > schedule + prod_duration && line_prod_duration < working duration
					if order['duedate'] >= plan_schedule[-1]['end_datetime'] + timedelta(minutes=line_dur['duration']) and plan_totaldur + line_dur['duration'] <= m_sessions[session_id]['work_duration']:
						if order['type_product'] == plan_schedule[-1]['type_product']:
							#no dandory
							dandory_time = 0
						else:
							dandory_time = 5
						
						# adjust_breaktime -> (session, starttime, duration, dandory_time)
						starttime, endtime = adjust_breaktime(m_sessions[session_id], plan_schedule[-1]['end_datetime'], line_dur['duration'], dandory_time)
						temp = {
							'key':order['key'],
							'line':line_dur['line'],
							'part_number': order['part_number'],
							'qty': order['qty'],
							'production_time_minutes': line_dur['duration'],
							'start_datetime': plan_schedule[-1]['end_datetime'],
							'end_datetime': (plan_schedule[-1]['end_datetime'] + timedelta(minutes=line_dur['duration'])),
							'duedate': order['duedate'],
							"ship_to_party" : order['ship_to_party'],
							"sold_to_party" : order['sold_to_party'],
							"production_version": production_version,
							"type_product" : order['type_product'],
							"material_needs" : [{"MCode":material['MCode'],"MName":material['MName'],"Mqty":int(material['Mqty'])*order["qty"]} for material in materials_],
						}
						order_occupied = True
						plan_schedule.append(temp)
						plan[line_dur['line']]['total_dur'] = plan_totaldur + line_dur['duration'] +dandory_time
						break
					else:
						order_failed = True
						order_temp = order.copy()
						order_temp['failed_reason'] = "duedate overdue"
						plan_failed.append(order_temp)

			# print(order['key'], " order occupied : ", order_occupied, " order failed : ", order_failed)

			if order_occupied or order_failed:
				break

		# if order doesn't match any schedule considered as plan failed
		if not order_occupied and not order_failed:
			order_temp = order.copy()
			order_temp['failed_reason'] = "order_not_match_any_schedule"
			plan_failed.append(order_temp)

	# # Plan for overtime
	for idx, order in enumerate(plan_failed):
		matt= [(d['production_version'],d['material']) for d in m_materials if d['part_number'] == order['part_number']]
		production_version = matt[0][0]
		materials_ = matt[0][1]
		for session_id, plan in vproduction_plan.items():
			for line_dur in order['duration_time']:
				try:
					plan_schedule = plan[line_dur['line']]['schedule']
					plan_totaldur = plan[line_dur['line']]['total_dur']
				except:
					continue

				if not plan_schedule:
					continue
				else:
					# print(session_id)
					# Initial plan production (first order insert)
					if (order['duedate'] > plan_schedule[-1]['end_datetime'] + timedelta(minutes=line_dur['duration']) and 
					plan_totaldur + line_dur['duration'] < m_sessions[session_id]['work_duration'] + m_sessions[session_id]['max_overtime']):
						if order['type_product'] == plan_schedule[-1]['type_product']:
							#no dandory
							dandory_time = 0
						else:
							dandory_time = 5

						# adjust_breaktime -> (session, starttime, duration, dandory_time)
						starttime, endtime = adjust_breaktime(m_sessions[session_id], plan_schedule[-1]['end_datetime'], line_dur['duration'], dandory_time)
						temp = {
							'key':order['key'],
							'line':line_dur['line'],
							'part_number': order['part_number'],
							'qty': order['qty'],
							'production_time_minutes': line_dur['duration'],
							'start_datetime': plan_schedule[-1]['end_datetime'],
							'end_datetime': (plan_schedule[-1]['end_datetime'] + timedelta(minutes=line_dur['duration'])),
							'duedate': order['duedate'],
							"ship_to_party" : order['ship_to_party'],
							"sold_to_party" : order['sold_to_party'],
							"production_version": production_version,
							"type_product" : order['type_product'],
							"material_needs" : [{"MCode":material['MCode'],"MName":material['MName'],"Mqty":int(material['Mqty'])*order["qty"]} for material in materials_],
						}
						order_occupied = True
						plan_schedule.append(temp)
						plan[line_dur['line']]['total_dur'] = plan_totaldur + line_dur['duration'] +dandory_time
						plan[line_dur['line']]['overtime'] = plan[line_dur['line']]['total_dur'] - m_sessions[session_id]['work_duration']
						del plan_failed[idx]
						break
						
	for order in plan_failed:
		for material in m_materials:
			if material['part_number'] == order['part_number']:
				new_cur_stock = material['current_stock'] - order['qty']
				if new_cur_stock >= 0 :
					material['current_stock'] = new_cur_stock
					order['status'] = "take from stock"
					break
				else:
					break


	# print(vproduction_plan)
	# print(len(m_orders) - len(plan_failed))
	# print('=========== Failed')
	# print(plan_failed)
	# print(len(plan_failed))



	# # Make to Stock
	for material in m_materials:
		order_occupied = False
		qty_total = 0
		min_unit_prod = material['minimum_unit_production']
		if min_unit_prod == 0:
			min_unit_prod = 1

		if len(line_overload) >= sessionxline:
			break

		if material['current_stock'] >= material['threshold']:
			continue

		if material['diff'] < 0:
			qty_needs = abs(material['diff'])
			qty_needs = qty_needs + (min_unit_prod - qty_needs % min_unit_prod) # adjusted to min unit prod
			pass
		else:
			qty_needs = (material['max_stock'] + material['min_stock'])/2 - material['current_stock']
			qty_needs = qty_needs - qty_needs % min_unit_prod # adjusted to min unit prod


		material['prefered_line'] = [{'line_id':i, 'line':key, 'sph':val} for i, line in enumerate(material['prefered_line'],start=1) for key, val in line.items()]

		for session_id, plan in vproduction_plan.items():
			for line in material['prefered_line']:
				if (session_id, line['line']) not in line_overload:
					try:
						plan_schedule = plan[line['line']]['schedule']
						plan_totaldur = plan[line['line']]['total_dur']
						plan_overtime = plan[line['line']]['overtime']
					except:
						continue
					
					qty = qty_needs - qty_total
					
					# print(material['part_number'], " ", line['line'], " qty_total: ", qty_total, " qty_needs: ", qty_needs, " qty: ", qty)
					line_prod_duration = round((qty/line['sph'])*60)

					if plan_overtime == 0:
						remain_dur = m_sessions[session_id]['work_duration'] - plan_totaldur
					else:
						remain_dur = min_overtime - (plan_overtime % min_overtime)

					if remain_dur - dandory_time > 0:
						remain_dur = remain_dur - dandory_time
					else:
						continue

					if not plan_schedule:
						starttime = m_sessions[session_id]['start_datetime']
					else:
						starttime = plan_schedule[-1]['end_datetime']
					
					if plan_schedule[-1]['type_product'] == material['type_product']:
						dandory_time = 0
					else :
						dandory_time = 5

					if line_prod_duration < remain_dur:

						# adjust_breaktime -> (session, starttime, duration, dandory_time)
						starttime, endtime = adjust_breaktime(m_sessions[session_id], starttime, line_prod_duration, dandory_time)

						# stock filled in one go
						temp = {
								'key': str(session_id)+'-'+material['part_number']+'-'+starttime.strftime('%d-%b-%Y'),
								'line': line['line'],
								'part_number': material['part_number'],
								'qty': qty,
								'production_time_minutes': line_prod_duration,
								'start_datetime': starttime,
								'end_datetime': (starttime + timedelta(minutes=line_prod_duration)),
								'duedate': '',
								'ship_to_party' : "ASKI WH",
								'sold_to_party' : "ASKI WH",
								"production_version": material['production_version'],
								"type_product" : material['type_product'],
								"material_needs" : [{"MCode":material['MCode'],"MName":material['MName'],"Mqty":int(material['Mqty'])*qty} for material in material['material']]
						}
						plan_schedule.append(temp)
						plan[line['line']]['total_dur'] = plan_totaldur + line_prod_duration + dandory_time
						if plan_totaldur != 0 and plan[line['line']]['overtime'] != 0:
							plan[line['line']]['overtime'] = plan[line['line']]['total_dur'] - m_sessions[session_id]['work_duration']
						order_occupied = True
						break
					else:
						# stock filled partially
						qty = (remain_dur/60) * line['sph']
						qty = qty - (qty % min_unit_prod)
						line_prod_duration = round((qty/line['sph'])*60)

						# ini handle bugs jika ada qty yang di produksi = 0
						# if qty == 0:
						# 	continue

						qty_total += qty

						# adjust_breaktime -> (session, starttime, duration, dandory_time)
						starttime, endtime = adjust_breaktime(m_sessions[session_id], starttime, line_prod_duration, dandory_time)

						temp = {
								'key': str(session_id)+'-'+material['part_number']+'-'+starttime.strftime('%d-%b-%Y'),
								'line': line['line'],
								'part_number': material['part_number'],
								'qty': qty,
								'production_time_minutes': line_prod_duration,
								'start_datetime': starttime,
								'end_datetime': (starttime + timedelta(minutes=line_prod_duration)),
								'duedate': '',
								'ship_to_party' : "ASKI WH",
								'sold_to_party' : "ASKI WH",
								"production_version": material['production_version'],
								"type_product" : material['type_product'],
								"material_needs" : [{"MCode":material['MCode'],"MName":material['MName'],"Mqty":int(material['Mqty'])*qty} for material in material['material']]
						}
						plan_schedule.append(temp)
						plan[line['line']]['total_dur'] = plan_totaldur + line_prod_duration + dandory_time

						if plan[line['line']]['overtime'] != 0:
							plan[line['line']]['overtime'] = plan[line['line']]['total_dur'] - m_sessions[session_id]['work_duration']

					if (remain_dur - line_prod_duration) <= dandory_time:
						line_overload.add((session_id, line['line']))
						# print(line_overload) # buat debug check line yang uda overload

			if order_occupied:
				break

	# print(vproduction_plan)
	# print(plan_failed)
	# print('=======')
	# print(m_materials)
	output = mapping_output(vproduction_plan,plan_failed,data)
	print(output)

	return output

def mapping_output(vproduction_plan,plan_failed,data):
	response = {}
	uid = str(uuid.uuid4())
	response['uuid'] = uid
	response['assy_id'] = data['assy_id']
	output = {}
	for shift_name,shift_content in vproduction_plan.items():
		for line_prod,line_content in shift_content.items():
			if line_prod not in output.keys():
				output[line_prod] = {}
			output[line_prod]['line_id'] = line_content['line_id']
			if shift_name not in output[line_prod].keys():
				output[line_prod][shift_name] = {}

			
			output[line_prod][shift_name]['shift_id'] = [d['id'] for d in data['shift_time']['shift_list'] if d['name']==shift_name][0]
			output[line_prod][shift_name]['production_plan'] = line_content['schedule']
			output[line_prod][shift_name]['overtime_duration'] = line_content['overtime']
			output[line_prod][shift_name]['total_dur'] = line_content['total_dur']
	output['failed_to_plan'] = plan_failed
	response[data['assy_type']] = output
	return response

autoplanning(data)

