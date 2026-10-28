import os
import re
import sys
import csv
import json
import time
import serial
import configparser
from datetime import datetime

from PyQt6 import uic
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox, QTableWidgetItem
from PyQt6.QtGui import QImage, QPixmap, QShortcut, QKeySequence
from PyQt6.QtCore import QTimer, Qt

from helper_classes import SettingsBox, TestResultsWindow, Camera
from database import DatabaseConn

# logger = logging.getLogger(__name__)
def get_path(raw_path):
    try:
        ret = os.path.join(sys._MEIPASS, raw_path)
    except AttributeError:
        ret = os.path.join('.', raw_path)
    return ret

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        uic.loadUi(get_path('layout/main_window.ui'), self)
        kaertech_logo = QImage(get_path(r"static/kaertech_logo.png")).scaled(90, 90, transformMode=Qt.TransformationMode.SmoothTransformation)
        self.logo_left.setPixmap(QPixmap.fromImage(kaertech_logo))
        # ddg_logo = QImage(get_path(r"static/ddg_logo.jpg")).scaledToWidth(160, Qt.TransformationMode.SmoothTransformation)
        # self.logo_right.setPixmap(QPixmap.fromImage(ddg_logo))

        self.config = {}
        self.csv_headers = []
        self.login_details = {'operator_en': '', 'shift': 'DEV_MODE'}

        self.load_config()
        self.roi_locations = self.load_roi_template()
        self.test_parameters = self.load_test_params()
        self.camera = Camera(self.config['cam_source'], self.config['cam_params'])

        self.hw_timer = QTimer(self)
        self.hw_timer.timeout.connect(self.update_current)
        self.connect_port()

        self.results_win = TestResultsWindow(self.roi_locations, self.test_parameters)
        self.bar_step = 100 / (len(self.test_parameters)+1 * 3)
        self.init_test_variables()
        self.start_test_button.setEnabled(False)

        self.tableWidget.setColumnWidth(0, 150)
        self.tableWidget.setColumnWidth(1, 100)
        self.tableWidget.setColumnWidth(2, 160)
        self.tableWidget.setColumnWidth(3, 100)
        self.tableWidget.setColumnWidth(4, 80)
        self.tableWidget.setColumnWidth(5, 160)

        # timer = QTimer(self)
        # timer.timeout.connect(self.update_time)
        # timer.start(1000)

        # Connect button click signal to slot
        self.login_button.clicked.connect(self.login)
        self.start_test_button.clicked.connect(self.start_test)
        self.serialno_display.returnPressed.connect(self.validate_serial)
        self.settings_button.clicked.connect(self.open_settings)
        self.abort_button.clicked.connect(self.abort_signal)
        # self.exit_button.clicked.connect(self.close)

        self.results_win.roi_done_signal.connect(self.update_test_progress)
        self.results_win.test_complete_signal.connect(self.mode_test_done)

        # Keyboard shortcuts
        self.initialize_shortcuts()

    # def update_time(self):
    #     now = datetime.now()
    #     self.date_display.setText(now.date().strftime(r'%m/%d/%Y'))
    #     self.time_display.setText(now.time().strftime(r'%I:%M:%S %p'))

        # if now.time().strftime(r'%I:%M:%S') == '07:00:00':
        #     self.is_golden_tested = False
    
    def initialize_shortcuts(self):
        SC_start_test = QShortcut(QKeySequence("F12"), self)
        SC_start_test.activated.connect(self.start_test)

        SC_open_settings = QShortcut(QKeySequence("F10"), self)
        SC_open_settings.activated.connect(self.open_settings)

        SC_abort_test = QShortcut(QKeySequence("F8"), self)
        SC_abort_test.activated.connect(self.abort_signal)

        SC_exit_app = QShortcut(QKeySequence("End"), self)
        SC_exit_app.activated.connect(self.close)

    def login(self):
        if self.login_button.text() == 'LOGIN':
            user = self.oprtr_en_input.text()
            shift = self.shift_input.currentText()

            if user and shift:
                self.login_details['operator_en'] = user.strip()
                self.login_details['shift'] = shift.strip()
                self.oprtr_en_input.setEnabled(False)
                self.shift_input.setEnabled(False)
                self.login_button.setText('LOGOUT')
            else:
                QMessageBox.information(self, 'Info', 'Please enter all details', buttons=QMessageBox.StandardButton.Ok)
        else:
            self.login_details['operator_en'] = 'DEV_MODE'
            self.login_details['shift'] = ''
            self.oprtr_en_input.setEnabled(True)
            self.shift_input.setEnabled(True)
            self.login_button.setText('LOGIN')

    def connect_port(self):
        try:
            self.hw_serial = serial.Serial(port = self.config['hw_port'], baudrate = int(self.config['baud_rate']))
            self.hw_serial.reset_input_buffer()
            self.hw_serial.reset_output_buffer()
            self.hw_serial.write(b'OUT1\r\n')
            self.hw_serial.flush()
            self.hw_timer.start(1000)
        except Exception as e:
            print(e)
            self.hw_serial = None
            self.opcurr_display.setText("PS Not Connected")
    
    def open_settings(self):
        mode_box = SettingsBox(self.config['template_path'], self.camera)
        result = mode_box.exec()

        if result:
            if mode_box.mode == 'DEVELOPMENT':
                self.mode_label.setText('DEVELOPMENT')
                self.mode_label.setStyleSheet("background-color: rgb(170, 255, 255);")
            else:
                self.mode_label.setText('PRODUCTION')
                self.mode_label.setStyleSheet("background-color: rgb(255, 255, 0);")

            old_cam = self.config['cam_source']
            old_serial = self.config['hw_port']
            old_baud = self.config['baud_rate']
            self.load_config()
            self.roi_locations = self.load_roi_template()
            self.test_parameters = self.load_test_params()
            self.init_test_variables()
            if self.config['cam_source'] != old_cam:
                self.camera = Camera(index=self.config['cam_source'])
            if self.config['hw_port'] != old_serial or self.config['baud_rate'] != old_baud:
                self.connect_port()

    def abort_signal(self):
        self.results_win.abort_test()

    def enable_buttons(self, state: bool):
        if state == False:
            self.start_test_button.setEnabled(state)
        self.settings_button.setEnabled(state)
        self.abort_button.setEnabled(not state)

    def init_test_variables(self):
        self.current_serial = ''
        self.head_details = {'serial_num': '', 'po_num': '', 'operator_en': '', 'shift': '', 'date_time': ''}
        self.curr_values = {}
        self.final_values = {}
        self.tail_details = {'duration': '', 'test_rep': '', 'status': '', 'fail_reason': ''}
        self.fail_reasons = []

        self.start_test_time = 0
        self.progress_percent = 0

        self.test_progress.reset()
        self.tableWidget.clearContents()
        self.pass_fail_output.setText("")
        self.pass_fail_output.setStyleSheet("background-color: rgb(180, 180, 180);")
        # self.enable_buttons(True)

    def test_end(self):
        self.enable_buttons(True)
        self.messages.setText('Enter serial number to start testing')
        self.ponum_display.setText('')
        self.serialno_display.setText('')
        self.serialno_display.setFocus()

    def upload_results(self):
        # if self.mode_label.text() == 'DEVELOPMENT':
        #     self.write_csv()
        # elif self.config['golden_check'] and not self.is_golden_tested and self.current_serial in self.golden_serials:
        #     t_code = self.config['tester_code']
        #     self.dbconn.log_golden_test(t_code, self.current_serial, self.tail_details['status'])
        #     self.is_golden_tested = True
        # else:
        self.write_csv()
        if self.mode_label.text() == 'PRODUCTION':
            try:
                success, error_code = self.dbconn.db_insert(self.head_details, self.final_values, self.tail_details)
                if not success:
                    QMessageBox.critical(self, 'Error', error_code, buttons=QMessageBox.StandardButton.Ok)
            except:
                # self.write_csv()
                pass

        QTimer.singleShot(10, self.test_end)

    def update_current(self):
        if not self.hw_serial or not self.hw_serial.is_open:
            self.opcurr_display.setText("Disconnected")
            return

        current = self.read_current()
        if current is not None:
            self.opcurr_display.setText(f"{current:.3f} mA")
        else:
            self.opcurr_display.setText("No Reading")

    def read_current(self):
        if not self.hw_serial:
            return None
        try:
            self.hw_serial.reset_input_buffer()
            self.hw_serial.reset_output_buffer()
            self.hw_serial.write(b'IOUT2?\r\n')
            self.hw_serial.flush()
            response = self.hw_serial.readline().decode('ascii', errors='ignore').strip()
            if not response:
                response = self.hw_serial.read_until(expected=b'\n', size=100).decode('ascii', errors='ignore').strip()

            match = re.search(r'[-+]?\d*\.?\d+(?:[Ee][-+]?\d+)?', response)
            if not match:
                return None

            return float(match.group(0)) * 1000 # output in Amperes, convert to mA for limits
        except Exception:
            self.hw_serial.write(b'ERR?\r\n')
            self.hw_serial.flush()
            response = self.hw_serial.readline().decode('ascii', errors='ignore').strip()
            return None

    def process_values(self):
        self.activateWindow()
        self.final_values = self.results_win.final_values
        self.final_values.update(self.curr_values)
        # print(self.final_values)
        self.fail_reasons = self.results_win.fail_reasons

        if len(self.final_values) > 0 and len(self.fail_reasons) == 0:
            self.tail_details['status'] = 1
            self.pass_fail_output.setText("PASS")
            self.pass_fail_output.setStyleSheet("background-color: rgb(20, 230, 20);")
        else:
            self.tail_details['status'] = 0
            fr_raw = 'FAIL: ' + ','.join(self.fail_reasons)
            self.tail_details['fail_reason'] = fr_raw[:255]
            self.pass_fail_output.setText("FAIL")
            self.pass_fail_output.setStyleSheet("background-color: rgb(245, 0, 0);")

        test_duration = time.time() - self.start_test_time
        self.duration_display.setText(f"{test_duration:.4f}")
        self.tail_details['duration'] = f"{test_duration:.2f}"

        QTimer.singleShot(10, self.upload_results)

    def add_curr_value(self, mode, curr_meas):
        curr_param = f'mode{mode}_curr'
        self.curr_values[curr_param] = curr_meas

        # row = len(self.roi_locations) + (mode*2) - 2 # 1 = 16, 2 = 18, 3 = 20
        high = float(self.test_parameters[curr_param]['high'])
        low = float(self.test_parameters[curr_param]['low'])
        unit = self.test_parameters[curr_param]['unit']

        if not curr_meas:
            status = 'FAIL'
        elif curr_meas > low and curr_meas < high:
            status = 'PASS'
        else:
            status = 'FAIL'
        row = (mode*17) - 1
        self.update_test_progress(row, curr_param, high, curr_meas, low, unit, status)

    def mode_test_done(self, mode, status, fail_reason):
        self.messages.setText('Reading current...')
        hw_result = self.read_current()
        self.add_curr_value(mode, hw_result)

        if mode == 1:
            res = QMessageBox.information(self.results_win, 'MODE 1', f"MODE 1 {status}.\nEnable Mode 2 then click 'OK' ", QMessageBox.StandardButton.Ok)
            # self.results_win.activateWindow()
            success, frame = self.camera.get_image()
            if not success:
                QMessageBox.critical(self, 'Error', frame, buttons=QMessageBox.StandardButton.Ok)
                return
            self.results_win.add_image(frame)
            return
        elif mode == 2:
            res = QMessageBox.information(self.results_win, 'MODE 2', f"MODE 2 {status}.\nEnable Mode 3 then click 'OK' ", QMessageBox.StandardButton.Ok)
            # self.results_win.activateWindow()
            success, frame = self.camera.get_image()
            if not success:
                QMessageBox.critical(self, 'Error', frame, buttons=QMessageBox.StandardButton.Ok)
                return
            self.results_win.add_image(frame)
            return
        else:
            QTimer.singleShot(10, self.process_values)

    def update_test_progress(self, row, param_name, high, value, low, unit, status):
        self.tableWidget.setItem(row, 0, QTableWidgetItem(param_name))
        self.tableWidget.setItem(row, 1, QTableWidgetItem(str(high)))
        self.tableWidget.setItem(row, 2, QTableWidgetItem(str(value)))
        self.tableWidget.setItem(row, 3, QTableWidgetItem(str(low)))
        self.tableWidget.setItem(row, 4, QTableWidgetItem(unit))
        self.tableWidget.setItem(row, 5, QTableWidgetItem(status))

        # curr_value = self.tableWidget.item(row, 2)
        # if curr_value:
        #     value = curr_value.text() + ', ' + str(value)
        # self.tableWidget.setItem(row, 2, QTableWidgetItem(str(value)))

        # curr_entry = self.tableWidget.item(row, 5)
        # if curr_entry:
        #     status = curr_entry.text() + ', ' + status
        # self.tableWidget.setItem(row, 5, QTableWidgetItem(status))

        self.progress_percent += self.bar_step
        self.test_progress.setValue(int(self.progress_percent))

    def start_test(self):
        if self.current_serial == '':
            val = self.validate_serial()
            if not val:
                return

        self.head_details['serial_num'] = self.current_serial
        self.head_details['po_num'] = self.ponum_display.text()
        self.head_details.update(self.login_details)
        self.enable_buttons(False)
        self.messages.setText("Test in progress")
        self.start_test_time = time.time()

        # Capture image from camera
        success, frame = self.camera.get_image()
        if not success:
            QMessageBox.critical(self, 'Error', frame, buttons=QMessageBox.StandardButton.Ok)
            return

        if not self.results_win.isVisible():
            self.results_win.show()
        success = self.results_win.add_image(frame)
        if not success:
            return
        
        self.results_win.activateWindow()

        try:
            if not self.hw_serial.is_open:
                self.hw_serial.open()
        except:
            pass

        now = datetime.now()
        self.head_details['date_time'] = now.strftime(r"%Y-%m-%d %H:%M:%S")
        self.results_win.start_test_loop()
    
    def validate_serial(self):
        if self.login_button.text() == 'Login':
            QMessageBox.information(self, 'Info', 'Please login to start testing', buttons=QMessageBox.StandardButton.Ok)
            self.oprtr_en_input.setFocus()
            return False

        self.init_test_variables()
        self.current_serial = self.serialno_display.text().strip()
        if not self.current_serial:
            QMessageBox.information(self, 'Info', 'Enter serial number to test', buttons=QMessageBox.StandardButton.Ok)
            self.serialno_display.setFocus()
            return False

        if not self.hw_serial and self.mode_label == 'PRODUCTION':
            QMessageBox.critical(self, 'Error', 'Power Supply NOT connected', buttons=QMessageBox.StandardButton.Ok)
            return False

        # regex = re.fullmatch(r"K/d{11}", self.current_serial)
        # if self.mode_label.text() == 'PRODUCTION' and not regex:
        #     QMessageBox.information(self, 'Info', 'Incorrect serial format', buttons=QMessageBox.StandardButton.Ok)
        #     return False

        po_flag, po_error = self.dbconn.prev_station_ok(self.current_serial)
        if not po_flag:
            QMessageBox.warning(self, 'Warning', po_error, buttons=QMessageBox.StandardButton.Ok)
            if self.mode_label.text() == 'PRODUCTION':
                return False
        else:
            self.ponum_display.setText(po_error)

        # Dev Mode
        if self.mode_label.text() == 'DEVELOPMENT':
            self.tail_details['test_rep'] = 1
        # Prod Mode
        else:
            test_rep = self.dbconn.check_retest(self.current_serial)
            if test_rep >= 0 and test_rep < 3:
                self.tail_details['test_rep'] = test_rep + 1
            elif test_rep == -1:
                QMessageBox.warning(self, 'Warning', 'Unit already passed', buttons=QMessageBox.StandardButton.Ok)
                return False
            elif test_rep > 2:
                QMessageBox.warning(self, 'Warning', 'No more retests allowed', buttons=QMessageBox.StandardButton.Ok)
                return False
            else:
                QMessageBox.critical(self, 'Error', test_rep, buttons=QMessageBox.StandardButton.Ok)
                return False
            
        self.start_test_button.setEnabled(True)
        return True

    def load_config(self):
        parser = configparser.ConfigParser()
        parser.read('Settings.ini')

        self.config = {}
        for key, raw_value in parser['Settings'].items():
            key_std = key.lower().replace(' ', '_')
            try:
                value = int(raw_value.strip('"'))
            except ValueError:
                value = raw_value.strip('"')
                if value == 'None':
                    value = ''
            self.config[key_std] = value

        if 'Database' in parser.sections():
            db_host = parser.get('Database', 'Host')
            db_schema = parser.get('Database', 'Customer')
            db_model = parser.get('Database', 'Model')
            db_station = parser.get('Database', 'Station')

            self.config['model'] = db_model
            self.config['database'] = {'host': db_host, 'customer': db_schema, 'model': db_model, 'station': db_station}
            self.dbconn = DatabaseConn(db_host, db_schema, db_model, db_station)
            self.model_display.setText(db_model)
        else:
            self.config['database'] = None
            self.dbconn = None

        if 'Camera' in parser.sections():
            cam_src = parser.getint('Camera', 'Source')
            cam_exp = parser.getint('Camera', 'Exposure')
            cam_focus = parser.getint('Camera', 'Focus')
            cam_zoom = parser.getint('Camera', 'Zoom')
            self.config['cam_source'] = cam_src
            self.config['cam_params'] = {'exposure': cam_exp, 'focus': cam_focus, 'zoom': cam_zoom}
        else:
            self.config['cam_source'] = 0
            self.config['cam_params'] = {'exposure': -5, 'focus': 0, 'zoom': 100}

    def load_roi_template(self):
        coords_file = self.config['template_path']

        coordinates = {}
        with open(coords_file, 'r') as txtfile:
            for line in txtfile:
                matches = re.findall(r"\d+", line)
                if not matches:
                    continue

                name = 'LED' + matches[0]
                x = int(matches[1])
                y = int(matches[2])
                if len(matches) == 3:
                    coordinates[name] = (x, y)
                elif len(matches) == 5:
                    width = int(matches[3])
                    height = int(matches[4])
                    coordinates[name] = (x, y, width, height)
        
        # print(coordinates)
        return coordinates

    def load_test_params(self):
        params_file =self.config['limits_path']

        with open(params_file, 'r') as limits:
            test_params = json.load(limits)
            key_idx = [key for key in test_params.keys()]
            for i in range(len(key_idx)):
                key_name = key_idx[i]
                test_params[key_name].update({'table_row': i+1})

        # print(test_params)
        self.tableWidget.setRowCount(51)
        return test_params

    def write_csv(self):
        if len(self.csv_headers) == 0:
            self.csv_headers = ['serial_num', 'po_num', 'operator_en', 'shift', 'date_time']
            tailers = ['duration', 'test_rep', 'status', 'fail_reason']
            self.csv_headers.extend(self.final_values.keys())
            self.csv_headers.extend(tailers)
            # print('--- csv head:', self.csv_headers)

        now = datetime.now()
        if self.mode_label.text() == 'DEVELOPMENT':
            dayfile = f"DEV/results_{now.date().isoformat()}.csv"
        else:
            dayfile = f"results_{now.date().isoformat()}.csv"
        if not os.path.exists(dayfile):
            with open(dayfile, 'w', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=self.csv_headers, extrasaction='ignore')
                writer.writeheader()
    
        with open(dayfile, 'a', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=self.csv_headers, extrasaction='ignore')
            final = self.head_details | self.final_values | self.tail_details
            # print(final)
            writer.writerow(final)

    def closeEvent(self, event):
        if not self.isVisible():
            self.show()
        if not self.isActiveWindow():
            self.activateWindow()
        result = QMessageBox.warning(self, 'Warning', 'Are you sure you want to exit?', 
                            buttons=QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        # print(result)

        if result == QMessageBox.StandardButton.Yes:
            self.results_win.destroy()
            event.accept()
        else:
            event.ignore()

if __name__=='__main__':
    # print('Launched: Dental Monitoring LED Test v.0.0.0.0')
    # logging.basicConfig(filename='./logs/info.log',
    #                     format='%(asctime)s %(levelname)-8s %(message)s',
    #                     level=logging.INFO,
    #                     datefmt='%Y-%m-%d %H:%M:%S')

    app = QApplication(sys.argv)
    app.setStyle('windowsvista')
    window = MainWindow()
    window.show()

    # Start the event loop.
    sys.exit(app.exec())