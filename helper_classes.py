import os
import re
import sys
import cv2
import numpy as np
import pyqtgraph as pg

from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem
from PyQt6.QtGui import QPen, QFont, QColor, QPixmap, QImage
from PyQt6.QtCore import Qt, QRectF, pyqtSignal, QTimer
from PyQt6 import uic

from roi_debug import DebugROI, get_path

# def get_path(raw_path):
#     try:
#         ret = os.path.join(sys._MEIPASS, raw_path)
#     except Exception:
#         ret = os.path.join('.', raw_path)
#     return ret

class Camera:
    def __init__(self, source: int, cam_params: dict):
        try:
            self.cap = cv2.VideoCapture(source, cv2.CAP_DSHOW)
            self.exposure = cam_params.get('exposure')
            self.focus = cam_params.get('focus')
            self.zoom = cam_params.get('zoom')
            self.frame = None

            # print(cam_params)

            # self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            # self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 960)
            self.cap.set(cv2.CAP_PROP_EXPOSURE, self.exposure)
            self.cap.set(cv2.CAP_PROP_FOCUS, self.focus)
            self.cap.set(cv2.CAP_PROP_ZOOM, self.zoom)

            _, frame = self.cap.read()
            # cv2.imshow('testing', frame)
            # cv2.waitKey(0)
        except Exception as e:
            # print(e)
            pass
    
    def get_image(self):
        try:
            # remove 20 frames from the buffer to get the most recent frame (as possible)
            for _ in range(20):
                self.cap.set(cv2.CAP_PROP_EXPOSURE, self.exposure)
                self.cap.set(cv2.CAP_PROP_FOCUS, self.focus)
                self.cap.set(cv2.CAP_PROP_ZOOM, self.zoom)
                ret, frame = self.cap.read()

            # print(frame.shape)
            self.frame = frame
            return True, self.frame
        except Exception as e:
            # print(e)
            return False, str(e)

class SettingsBox(QDialog):
    def __init__(self, template_path, cam_object: Camera):
        super().__init__()
        uic.loadUi(get_path('layout/settings_box.ui'), self)
        self.dev_button.clicked.connect(self.set_dev)
        self.prod_button.clicked.connect(self.set_prod)
        self.template_path = template_path
        self.camera = cam_object
        self.launchGUI_button.clicked.connect(self.launch_debugger)
        self.mode = ''

    def launch_debugger(self):
        roi_debugger = DebugROI()
        _, img = self.camera.get_image()
        flag = roi_debugger.setImage(img)
        if not flag:
            QMessageBox.warning(self, 'Camera Error', 'Camera failed to open')
            
        roi_debugger.importTable(self.template_path)
        roi_debugger.show()
        self.close()

    def set_dev(self):
        self.mode = 'DEVELOPMENT'
        self.accept()

    def set_prod(self):
        self.mode = 'PRODUCTION'
        self.accept()

class TestResultsWindow(QDialog):
    roi_visible = pyqtSignal(bool)
    roi_done_signal = pyqtSignal(int, str, float, float, float, str, str)
    test_complete_signal = pyqtSignal(int, str, str)

    def __init__(self, roi_locations: dict, test_parameters: dict):
        super().__init__()
        uic.loadUi(get_path('layout/test_results.ui'), self)

        self.full_image = None
        self.roi_size = 50
        self.binary_threshold = 180
        self.dbox = None
        self.zoomed_image.setText('')

        self.roi_locations = roi_locations
        self.roi_list = list(roi_locations.keys())
        self.index = 0
        self.mode_status = 'PASS'
        self.test_parameters = test_parameters
        self.final_values = {}
        self.fail_reasons = []
        self.abort = False

        self.scene = QGraphicsScene(0,0,1920,1080)
        # self.scene = Scene(0,0,1920,1080)
        self.graphicsView.setScene(self.scene)

        self.pen_yellow = QPen(QColor("yellow"), 5.0)
        self.pen_green = QPen(QColor().fromRgb(60, 255, 0), 5.0)
        self.pen_cyan = QPen(QColor("cyan"), 5.0)
        self.tfont=QFont()
        self.tfont.setPixelSize(25)

    def add_image(self, frame):
        self.full_image = frame
        self.scene.clear()
        contig_img = np.ascontiguousarray(frame)
        if frame is None:
            QMessageBox.critical(self, 'Camera Error', 'Camera failed to open')
            return False
        
        height, width, channel = frame.shape
        mat_qimage = QImage(contig_img.data, width, height, channel*width, QImage.Format.Format_BGR888)
        self.pic = QGraphicsPixmapItem()
        self.pic.setPixmap(QPixmap.fromImage(mat_qimage.copy()))
        self.scene.addItem(self.pic)
        self.scene.setSceneRect(0,0,width,height)
        rect = self.scene.itemsBoundingRect()
        self.graphicsView.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        return True

    def check_limits(self, led_name, value, low, high):
        num_match = re.search(r'\d+', led_name)
        if not num_match:
            return False, ''
        led_num = int(num_match.group(0))

        # Mode 1
        # 1-10
        if self.current_mode == 1:
            if led_num <= 10:
                if value <= low:
                    return False, 'LOW'
                elif value >= high:
                    return False, 'HIGH'
                else:
                    return True, 'OKAY'
            else:
                if value < low:
                    return True, 'N/A'
                else:
                    return False, 'STRAY'
        
        # Mode 2
        # 1-6, 11-16 
        if self.current_mode == 2:
            if (led_num % 10) > 0 and (led_num % 10) <= 6:
                if value <= low:
                    return False, 'LOW'
                elif value >= high:
                    return False, 'HIGH'
                else:
                    return True, 'OKAY'
            else:
                if value < low:
                    return True, 'N/A'
                else:
                    return False, 'STRAY'

        if self.current_mode == 3:
            if value <= low:
                return False, 'LOW'
            elif value >= high:
                return False, 'HIGH'
            else:
                return True, 'OKAY'

    def update_test(self):
        if self.abort:
            return
        
        if self.index == len(self.roi_list):
            errors = self.mode_fr[self.current_mode]
            val = '' if not errors else 'STRAY: ' + ','.join(errors)
            self.test_complete_signal.emit(self.current_mode, self.mode_status, val)
            self.index = 0
            self.current_mode += 1

        if self.current_mode > 3:
            # End of testing
            return

        led_name = self.roi_list[self.index]
        coordinates = self.roi_locations.get(led_name)
        # test_params = [key.split('_')[1] for key in self.test_parameters.keys() if key.split('_')[0] == led_name]
        value = self.show_results(led_name, coordinates)
        # print(led_name, value)

        # for param_name, value in roi_output.items():
        limits = self.test_parameters[led_name.lower()]
        high = float(limits['high'])
        low = float(limits['low'])

        passed, status = self.check_limits(led_name, value, low, high)
        if not passed:
            self.mode_status = 'FAIL'
            if status == 'STRAY':
                # txt = f"{led_name.upper()} {status}-{self.current_mode}"
                self.mode_fr[self.current_mode].append(led_name.upper())
            else:
                txt = f"{led_name.upper()} {status[0]}"
                self.fail_reasons.append(txt)
        # Display the results to the Main Window table
        idx = limits['table_row'] - 1 # led numbers start at 1 instead of 0
        self.roi_done_signal.emit(idx + (self.current_mode-1)*17, f"mode{self.current_mode}_{led_name.upper()}", high, value, low, limits['unit'], status)
        self.final_values[f"mode{self.current_mode}_{led_name.lower()}"] = value

        self.index += 1
        QTimer.singleShot(10, self.update_test)

    def abort_test(self):
        self.abort = True

    def start_test_loop(self):
        # reset to default
        self.final_values = {}
        self.fail_reasons = []
        self.index = 0
        self.current_mode = 1
        self.mode_status = 'PASS'
        self.mode_fr = {1:[], 2:[], 3:[]}

        QTimer.singleShot(0, self.update_test)
    
    def color_comp(self, cropped, test_params):
        total = np.sum(cropped)
        channel_sums = np.sum(cropped, axis=(0,1))
        # print(channel_sums)
        blue = float(100 * channel_sums[0] / total)
        green = float(100 * channel_sums[1] / total)
        red = float(100 * channel_sums[2] / total)
        print(f"{red:.5f}, {green:.5f}, {blue:.5f}")

        final = {}
        if 'red' in test_params:
            final['red'] = round(red, 5)
        if 'green' in test_params:
            final['green'] = round(green, 5)
        if 'blue' in test_params:
            final['blue'] = round(blue, 5)

        return final
    
    def show_results(self, roi_name: str, coordinates: tuple[int, int, int, int]):
        # Variables for convenience
        left = coordinates[0]
        top = coordinates[1]
        width = coordinates[2]
        height = coordinates[3]
        right = left + width
        bottom = top + height

        # Set text displays
        num_match = re.search(r'\d+', roi_name)
        raw_num = 0 if not num_match else int(num_match.group(0))
        self.led_name.setText(roi_name.upper())
        self.left_bound.setText(str(left))
        self.top_bound.setText(str(top))
        self.right_bound.setText(str(right))
        self.bottom_bound.setText(str(bottom))

        # Draw ROI
        rect_item = QGraphicsRectItem(QRectF(left, top, width, height))
        rect_item.setPen(self.pen_yellow)
        self.scene.addItem(rect_item)
        # print(raw_num)

        cropped = self.full_image[top:bottom, left:right]
        grayscale = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
        
        # Display zoomed ROI
        contig_img = np.ascontiguousarray(grayscale)
        height, width = grayscale.shape[:2]
        mat_qimage = QImage(contig_img.data, width, height, width, QImage.Format.Format_Grayscale8)
        resized_pixmap = QPixmap.fromImage(mat_qimage.copy()).scaled(125, 125, Qt.AspectRatioMode.KeepAspectRatio)
        self.zoomed_image.clear()
        self.zoomed_image.setPixmap(resized_pixmap)

        # Find area of largest object inside ROI
        ret, thresh1 = cv2.threshold(grayscale,self.binary_threshold,255,cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(thresh1, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        biggest_object = {'x': -1, 'y': -1, 'radius': -1, 'area': -1}
        for i, ctr in enumerate(contours):
            t_cop = np.zeros(cropped.shape[:2], dtype="uint8")
            cv2.drawContours(t_cop, [ctr], -1, (255, 255, 255), -1)
            pixarea = cv2.countNonZero(t_cop)

            (x,y), radius = cv2.minEnclosingCircle(ctr)
            if pixarea > biggest_object['area']:
                biggest_object = {'x': x, 'y': y, 'radius': radius, 'area': pixarea}
        # print('Biggest object @ ', biggest_object)

        # Get average and max values of histogram
        histogram = cv2.calcHist([grayscale], [0], None, [256], [0,256])
        total_intensity = 0
        max_intensity = 0
        max = 0
        for bin in range(0,256):
            total_intensity += (histogram[bin] * bin)
            if bin >= self.binary_threshold:
                if histogram[bin] > max:
                    max = int(histogram[bin])
                    max_intensity = bin
        
        max_area = biggest_object['area'] // 3
        results = {'intensity': max_area}

        # if len(test_params) > 1:
        #     color_values = self.color_comp(cropped, test_params[1:])
        #     results.update(color_values)
        
        # Set text displays
        self.dis_center_x.setText(f"{biggest_object['x']:.4f}")
        self.dis_center_y.setText(f"{biggest_object['y']:.4f}")
        self.intensity.setText(str(max_area))
        self.mean_value.setText(str(total_intensity // (self.roi_size*self.roi_size)))
        self.max_value.setText(str(max_intensity))

        # Scene container for histogram
        scene = QGraphicsScene()
        self.histogram_view.setScene(scene)

        # PlotWidget to show histogram
        plot_widget = pg.PlotWidget()
        plot_widget.plot([bin for bin in range(256)], histogram.flatten())
        plot_widget.getPlotItem().getAxis('left').setStyle(tickFont=self.tfont)
        plot_widget.getPlotItem().getAxis('bottom').setStyle(tickFont=self.tfont)
        scene.addWidget(plot_widget)
        rect = scene.itemsBoundingRect()
        self.histogram_view.fitInView(rect, Qt.AspectRatioMode.IgnoreAspectRatio)

        self.scene.removeItem(rect_item)
        rect_item.setPen(self.pen_green)
        self.scene.addItem(rect_item)

        # return results
        return max_area

    def closeEvent(self, event):
        # only hide the window to maintain the reference
        if self.parent():
            event.ignore()
            self.hide()

def helper():
    with open('template_coordinates_old2.csv', 'r') as file:
        final = [0 for _ in range(80)]
        for f in file:
            name, x, y = f.split(',')
            num = int(name[3:]) - 1
            x = round((int(x) * 0.94) + 220)
            y = round((int(y) * 0.94) - 30)
            final[num] = (x,y)

    with open('template_coordinates.csv', 'w') as file:
        for i, fin in enumerate(final, 1):
            # print(f"LED{i},{fin[0]},{fin[1]}")
            file.write(f"LED{i},{fin[0]},{fin[1]}\n")


if __name__=='__main__':
    import sys
    app = QApplication(sys.argv)
    app.setStyle('windowsvista')

    cap = cv2.VideoCapture(1, cv2.CAP_DSHOW)
    for _ in range(20):
        cap.set(cv2.CAP_PROP_EXPOSURE, -10)
        cap.set(cv2.CAP_PROP_FOCUS, 35)
        cap.set(cv2.CAP_PROP_ZOOM, 250)
        ret, image = cap.read()

    roi_locations = {'LED1': (234,379,70,70)}
    test_parameters = {"led1": {"high": 120, "low": 20, "unit": "", "table_row": 1}}

    # image = cv2.resize(cv2.rotate(image, cv2.ROTATE_180), None, fx=2.4, fy=2.4, interpolation=cv2.INTER_CUBIC)
    # image = cv2.imread('102558.jpg')
    win = TestResultsWindow(roi_locations, test_parameters)
    win.show()
    win.add_image(image)
    win.start_test_loop()

    sys.exit(app.exec())

    win.start_test_loop()