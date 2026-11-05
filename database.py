import os
from datetime import datetime, time, timedelta
import pymysql
from pymysql.cursors import DictCursor

DB_USER = os.getenv('DB_USER', 'labeling')
DB_PASSWORD = os.getenv('DB_PASSWORD', 'labeling')

class DatabaseConn:
    def __init__(self, db_host, schema: str, model: str, station: str):
        self.config = {
                        'host': db_host,
                        'user': DB_USER,
                        'password': DB_PASSWORD,
                        'autocommit': False,
                        'use_unicode': True,
                        'charset': 'utf8mb4'
                    }
        self.schema = schema
        self.model = model
        self.station = station
        self.previous_stations = []
        self.table_name = f"`{model}_{station}`"
        self.csv_headers = []

    def connect(self, schema: str):
        """Create database connection"""
        try:
            self.config['database'] = schema
            return pymysql.connect(**self.config)
        except pymysql.Error as e:
            # raise Exception(f"Database connection error: {e}")
            print(f"Database connection error: {e}")
            return None

    def is_golden_tested(self):
        try:
            with self.connect('te') as conn:
            # conn = self.connect('te')
                with conn.cursor(DictCursor) as cursor:
                    today = datetime.now()
                    am = time(7,0,0)
                    pm = time(19,0,0)
                    if today.time() >= am and today.time() < pm:
                        shift = [datetime.combine(today.date(), am), datetime.combine(today.date(), pm)]
                    else:
                        shift = [datetime.combine(today.date(), pm), datetime.combine(today.date()+timedelta(days=1), am)]

                    query = """ SELECT * FROM `golden_sample_logs` WHERE `date_time_tested` BETWEEN %s AND %s ORDER BY `date_time_tested` DESC """
                    cursor.execute(query, (shift[0], shift[1]))
                    logs = cursor.fetchall()

                    if len(logs) == 0:
                        # print('not yet tested')
                        return False
                    else:
                        return True
        except Exception as e:
            print(f'Error: {e}')
            # raise e

    def is_serial_golden(self, serial_num):
        try:
            with self.connect('te') as conn:
                cursor = conn.cursor(DictCursor)
                query = "SELECT * FROM `golden_sample_masterlist` WHERE `golden_sn` = %s"
                cursor.execute(query, (serial_num,))
                result = cursor.fetchall()

                if result:
                    return True
                else:
                    return False
            
        except Exception as e:
            print(f'Error: {e}')
            return False

    def log_golden_test(self, tester_code, serial_num, status):
        try:
            with self.connect('te') as conn:
                with conn.cursor(DictCursor) as cursor:
                    today = datetime.now()
                    query = " INSERT INTO `golden_sample_logs` VALUES (%s, %s, %s, %s, %s)"
                    cursor.execute(query, (tester_code, serial_num, self.model, today, status))
                    conn.commit()

        except Exception as e:
            print(f'Error: {e}')
            # raise e

    def prev_station_ok(self, serial_raw) -> tuple[bool, str]:
        serial_num = serial_raw.split('_')[0]
        try:
            with self.connect(self.schema) as conn:
                with conn.cursor(DictCursor) as cursor:
                    # Check if it exists on main
                    query = " SELECT * " + f" FROM `{self.model}_main`" + " WHERE `serial_num` LIKE %s"
                    cursor.execute(query, (f'{serial_num}',))
                    status_main = cursor.fetchall()

                    if len(status_main) == 0:
                        return False, "Error: Serial not in Database"
                    
                    if not self.previous_stations:
                        for key in status_main[0].keys():
                            if key == self.station:
                                break
                            elif (key != 'serial_num' and key != 'po_num'):
                                self.previous_stations.append(key)

                    fail_prev = []
                    for pst in self.previous_stations:
                        table = f"`{self.model}_{pst}`"
                        query = " SELECT `serial_num`, `status` FROM " + table + " WHERE `serial_num` LIKE %s "
                        cursor.execute(query, (f'{serial_num}',))
                        result = cursor.fetchall()
                        if len(result) == 0:
                            fail_prev.append(pst)
                        elif result[0]['status'] == 0 or status_main[0][pst] == 0:
                            fail_prev.append(pst)

                    if len(fail_prev) > 0:
                        return False, f"Error: Unit did not pass {', '.join(fail_prev)}"
                    else:
                        return True, status_main[0]['po_num']
        
        except pymysql.Error as e:
            # print(f'Error: {e}')
            return False, f'Error: {e}'

        except AttributeError:
            return False, ''
    
    def check_retest(self, serial_raw):
        serial_num = serial_raw.split('_')[0]
        try:
            with self.connect(self.schema) as conn:
                with conn.cursor(DictCursor) as cursor:

                    # Check if serial already exists in current station
                    query = " SELECT `serial_num`, `test_rep`, `status` FROM " + self.table_name + " WHERE `serial_num` LIKE %s ORDER BY `test_rep` DESC"
                    cursor.execute(query, (f'{serial_num}%',))
                    status_retest = cursor.fetchall()

                    if len(status_retest) > 0 and len(status_retest) < 3:
                        if status_retest[0]['status'] == 1:
                            return -1
                        # Update most recent entry
                        last_test_rep = status_retest[0]['test_rep']
                        new_serial = f'{serial_num}_{last_test_rep}'
                        query = " UPDATE `"+ self.table_name +"` SET `serial_num` = %s WHERE `serial_num` = %s "
                        # print('DEV_MODE:', query)
                        cursor.execute(query, (new_serial, serial_num))
                        conn.commit()
                    
                    return len(status_retest)
        except Exception as e:
            # print(f'Error: {e}')
            return str(e)

        except AttributeError:
            return False, ''

    def get_db_status(self, serial_num):
        try:
            with self.connect(self.schema) as dbconn:
                with dbconn.cursor(DictCursor) as cursor:
                    main_query = " SELECT * FROM " + f"`{self.model}_depanel`" + " WHERE `serial_num` = %s "
                    cursor.execute(main_query, (serial_num,))
                    depanel_status = cursor.fetchone()

                    if not depanel_status:
                        return {'error': "Serial Number not in Database"}

                    ponum = depanel_status['po_num']

                    if len(self.previous_stations) == 0:
                        col_query = "DESCRIBE " + f"`{self.model}_main`"
                        cursor.execute(col_query)
                        results = cursor.fetchall()
                        self.previous_stations = [col['Field'] for col in results]
                        print(self.previous_stations)

                    curr_test_rep = -1
                    failed_stations = []
                    for column in self.previous_stations:
                        if column == 'serial_num' or column == 'po_num':
                            continue
                        elif column == self.station:
                            retest_query = " SELECT `test_rep` FROM " + self.table_name + " WHERE `serial_num` LIKE %s ORDER BY `test_rep` DESC"
                            cursor.execute(retest_query, (f'{serial_num}%',))
                            curr_test_rep = len(cursor.fetchall()) + 1
                            break
                        else:
                            table_name = f"`{self.model}_{column}`"
                            sub_query = " SELECT `serial_num`, `status` FROM " + table_name + " WHERE `serial_num` = %s "
                            cursor.execute(sub_query, (serial_num,))
                            sub_status = cursor.fetchone()
                            if not sub_status or sub_status['status'] == 0:
                                failed_stations.append(column)

                    if len(failed_stations) > 0:
                        return {'error': "Unit did not pass " + ', '.join(failed_stations)}

                    if curr_test_rep > 3 and curr_test_rep % 3 == 1:
                        fa_query = " SELECT `serial_num`, `farepair_status` FROM `fa`.`main` WHERE `serial_num` = %s"
                        cursor.execute(fa_query, (serial_num,))
                        resp = cursor.fetchone()
                        if not resp or resp['farepair_status'] != 4:
                            return {'error': "No more retests allowed until FA"}
                    
                    return {'po_num': ponum, 'test_rep': curr_test_rep}

        except Exception as e:
            return {'error': str(e)}

    def update_last_entry(self, cursor: DictCursor, serial_num: str):
        get_query = " SELECT `serial_num`, `test_rep` FROM " + self.table_name + " WHERE `serial_num` LIKE %s ORDER BY `test_rep` DESC"
        cursor.execute(get_query, (f"{serial_num}%",))
        last_rep = cursor.fetchone()['test_rep']

        new_serial = f"{serial_num}_{last_rep}"
        set_query = " UPDATE "+ self.table_name +" SET `serial_num` = %s WHERE `serial_num` = %s "
        cursor.execute(set_query, (new_serial, serial_num))

    def db_insert(self, head_details: dict, data: dict, tail_details: dict):
        all_values = head_details | data | tail_details
        serial_num = head_details.get('serial_num')
        final_status = tail_details.get('status')
        
        try:
            with self.connect(self.schema) as conn:
                with conn.cursor(DictCursor) as cursor:
                    # if retest, update the last entry with underscore
                    if tail_details.get('test_rep', 1) > 1:
                        self.update_last_entry(cursor, serial_num)
                        
                    headers = ', '.join(all_values.keys())
                    s_set = ', '.join(['%s'] * len(all_values.keys()))
                    query = " INSERT INTO " + self.table_name + f" ({headers}) VALUES ({s_set})"

                    readback = 0
                    for _ in range(3):
                        if readback > 0:
                            break
                        cursor.execute(query, list(all_values.values()))
                        conn.commit()

                        readback_query = "SELECT * FROM " + self.table_name + " WHERE `serial_num` = %s"
                        cursor.execute(readback_query, (serial_num,))
                        readback = len(cursor.fetchall())

                    if readback > 0:
                        # Update main to reflect new status
                        query = f" UPDATE `{self.model}_main`" + " SET `"+ self.station +"` = %s WHERE `serial_num` = %s "
                        cursor.execute(query, (final_status, serial_num))
                        conn.commit()
                        return True, ''
                    else:
                        return False, 'Error writing to database'
        except Exception as e:
            return False, str(e)

# if __name__=='__main__':
#     dbconn = DatabaseConn()
#     while True:
#         serial_num = input('Enter serial:')
#         dbconn.retrieve_details('faceware', serial_num)