import os
import re
import cv2
import sys
import numpy as np
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QMainWindow, QDialog, QFileDialog, QGraphicsScene, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsItem, QListWidget
from PyQt6.QtGui import QPen, QColor, QPixmap, QImage, QStandardItemModel, QStandardItem
from PyQt6.QtCore import Qt, QRectF, pyqtSignal, QThread, QPersistentModelIndex
from PyQt6 import uic

def get_path(raw_path):
    try:
        ret = os.path.join(sys._MEIPASS, raw_path)
    except Exception:
        ret = os.path.join('.', raw_path)
    return ret

class RoiItem(QGraphicsRectItem):
    def __init__(self, x, y, width=120, height=120, parent=None):
        self.width = width
        self.height = height
        super().__init__(QRectF(x, y, width, height), parent)

        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable | 
                      QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges |
                      QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges)

        default = QPen(QColor("cyan"))
        default.setWidth(3)
        self.setPen(default)

        # self.inTable = False
        self.name = ''
        self.model_index = None

    def update_pen(self, color, width=3):
        new_pen = QPen(QColor(color))
        new_pen.setWidth(width)
        self.setPen(new_pen)

    def resizeWidth(self, new_value):
        try:
            diff = new_value - self.width
            coords = self.rect()
            self.setRect(coords.adjusted(0, 0, diff, 0))
            self.width = new_value
        except TypeError:
            pass

    def resizeHeight(self, new_value):
        try:
            diff = new_value - self.height
            coords = self.rect()
            self.setRect(coords.adjusted(0, 0, 0, diff))
            self.height = new_value
        except TypeError:
            pass

    def itemChange(self, change, value):
        if change == QGraphicsRectItem.GraphicsItemChange.ItemPositionChange and self.scene():
            new_pos = value
            scene_rect = self.scene().sceneRect()
            if not scene_rect.contains(new_pos):
                new_pos.setX(min(scene_rect.right()-self.width, max(new_pos.x(), scene_rect.left())))
                new_pos.setY(min(scene_rect.bottom()-self.height, max(new_pos.y(), scene_rect.top())))
                return new_pos

        return QGraphicsRectItem.itemChange(self, change, value)

class VideoThread(QThread):
    change_pixmap_signal = pyqtSignal(np.ndarray)

    def __init__(self, cam_object, cam_params: dict):
        super().__init__()
        self.cam_object = cam_object
        self.cam_params = cam_params

        exp  = cam_params.get('exposure')
        foc = cam_params.get('focus')
        zoom = cam_params.get('zoom')
        self.set_cam_prop(exp, foc, zoom)

    def read_cam_prop(self):
        # br = self.cap.get(cv2.CAP_PROP_BRIGHTNESS)
        # con = self.cap.get(cv2.CAP_PROP_CONTRAST)
        exp = self.cam_object.cap.get(cv2.CAP_PROP_EXPOSURE)
        zoom = self.cam_object.cap.get(cv2.CAP_PROP_ZOOM)
        foc = self.cam_object.cap.get(cv2.CAP_PROP_FOCUS)

        print('values', exp, foc, zoom)
        return exp, foc, zoom

    def set_cam_prop(self, exp=-5, foc=1, zoom=100):
        self.cam_exposure = exp
        self.focus = foc
        self.zoom = zoom
        # print('before', br, con, exp, foc, zoom)

    def run(self):
        self.cam_object.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cam_params.get('width', 640))
        self.cam_object.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cam_params.get('height', 480))

        while True:
            self.cam_object.cap.set(cv2.CAP_PROP_EXPOSURE, self.cam_exposure)
            self.cam_object.cap.set(cv2.CAP_PROP_FOCUS, self.focus)
            self.cam_object.cap.set(cv2.CAP_PROP_ZOOM, self.zoom)
            ret, frame = self.cam_object.cap.read()
            # if ret:
            #     adjusted = cv2.convertScaleAbs(frame, alpha=self.contrast, beta=self.brightness)
                # cv2.imwrite('image.jpg', adjusted)
            if ret:
                self.change_pixmap_signal.emit(frame)
        # self.cap.release()

    # def stop(self):
    #     self._run_flag = False
    #     self.wait()

class DebugROI(QDialog):
    new_cam_params = pyqtSignal(dict)
    def __init__(self, cam_object: cv2.VideoCapture, cam_params: dict):
        super().__init__()
        uic.loadUi(get_path('layout/debug_roi_compact.ui'), self)
        self.roi_size_default = 120
        self.roi_list: list[RoiItem] = []

        self.cam_params = cam_params
        self.orig_params = cam_params.copy()
        self.camera = cam_object
        width, height = self.init_spin_box(cam_params)

        self.exposure_input.valueChanged.connect(self.update_img_params)
        self.focus_input.valueChanged.connect(self.update_img_params)
        self.zoom_input.valueChanged.connect(self.update_img_params)

        self.scene = QGraphicsScene(0, 0, width, height)
        self.graphicsView.setScene(self.scene)
        rect = self.scene.sceneRect()
        self.graphicsView.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.scene.mouseMoveEvent = self.mouse_move_override
        self.scene.mousePressEvent = self.mouse_press_override
        self.scene.mouseReleaseEvent = self.mouse_release_override

        self.pixmapItem = QGraphicsPixmapItem()
        self.raw_image = None
        self.scene.addItem(self.pixmapItem)

        self.vthread = VideoThread(cam_object, cam_params)

        self.tableModel = QStandardItemModel()
        self.tableModel.setHorizontalHeaderItem(0, QStandardItem('Name'))
        self.tableModel.setHorizontalHeaderItem(1, QStandardItem('Coordinates'))
        self.tableModel.setHorizontalHeaderItem(2, QStandardItem('Size (w x h)'))
        self.tableView.setModel(self.tableModel)

        # self.imgReso_text.setText(f"1920x1080")
        self.orig_source = ''

        self.addRoi_button.clicked.connect(self.addRoiItem)
        self.roiName_input.returnPressed.connect(self.setRoiName)
        self.exportList_button.clicked.connect(self.exportTable)
        self.removeEntry_button.clicked.connect(self.removeRoi)
        self.removeEntry_button.setEnabled(False)

        self.zoomCtrl_slider.valueChanged.connect(self.updateZoom)
        self.resetZoom_button.clicked.connect(self.resetZoom)

        self.roiWidth.valueChanged.connect(self.updateWidth)
        self.roiHeight.valueChanged.connect(self.updateHeight)
        self.squareMode.checkStateChanged.connect(self.on_square)
        self.squareMode.setChecked(True)

    def showEvent(self, event):
        event.accept()
        if not self.vthread.isRunning():
            self.vthread.start()
        self.vthread.change_pixmap_signal.connect(self.update_scene)
        self.vthread.finished.connect(self.vthread.deleteLater)
        self.vthread.destroyed.connect(lambda: print("QThread instance was destroyed."))
        # self.resetZoom()

    def init_spin_box(self, config: dict):
        width = config.get('width', 0)
        height = config.get('height', 0)
        self.imgReso_text.setText(f"{width}x{height}")
        self.exposure_input.setValue(config.get('exposure', -5))
        self.focus_input.setValue(config.get('focus', 0))
        self.zoom_input.setValue(config.get('zoom', 100))
        return width, height

    def update_img_params(self):
        exp = self.exposure_input.value()
        foc = self.focus_input.value()
        zm = self.zoom_input.value()
        
        self.cam_params.update({'exposure': exp, 'focus': foc, 'zoom': zm})
        self.vthread.set_cam_prop(exp, foc, zm)

    def update_scene(self, cv_img):
        self.raw_image = cv_img
        try:
            h, w, ch = cv_img.shape
            bytes_per_line = ch * w
            qt_img = QImage(cv_img.data, w, h, bytes_per_line, QImage.Format.Format_BGR888)
            self.pixmapItem.setPixmap(QPixmap.fromImage(qt_img))
            # self.scene.setSceneRect(0,0,w,h)
            rect = self.scene.itemsBoundingRect()
            self.graphicsView.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        except:
            pass
    
    def update_entry(self, roi_item: RoiItem):
        name = self.roiName_input.text()
        x = self.xCoords.text()
        y = self.yCoords.text()
        w = self.roiWidth.value()
        h = self.roiHeight.value()
        if name:
            roi_item.name = name
        roi_item.resizeWidth(w)
        roi_item.resizeHeight(h)

        row = roi_item.model_index.row()
        self.tableModel.item(row, 0).setText(name)
        self.tableModel.item(row, 1).setText(f"{x}, {y}")
        self.tableModel.item(row, 2).setText(f"{w} x {h}")

    def mouse_release_override(self, event):
        # When user releases mouse after movement, update internal data and table entry
        QGraphicsScene.mouseReleaseEvent(self.scene, event)
        selected = self.scene.selectedItems()
        if len(selected) == 0:
            return
        item = selected[0]
        # if item.inTable:
        self.update_entry(item)
        QGraphicsRectItem.mouseReleaseEvent(item, event)

    def mouse_move_override(self, event):
        # Live update of ROI's position
        QGraphicsScene.mouseMoveEvent(self.scene, event)
        selected = self.scene.selectedItems()
        if len(selected) == 0:
            return
        item = selected[0]
        QGraphicsRectItem.mouseMoveEvent(item, event)
        self.xCoords.setText(f"{item.pos().x():.2f}")
        self.yCoords.setText(f"{item.pos().y():.2f}")

    def mouse_press_override(self, event):
        # Show ROI's internal data and highlight table entry if possible
        self.tableView.selectionModel().clearSelection()
        QGraphicsScene.mousePressEvent(self.scene, event)
        selected = self.scene.selectedItems()
        if len(selected) == 0:
            self.roiName_input.setText('')
            self.xCoords.setText('')
            self.yCoords.setText('')
            self.roiWidth.setValue(self.roi_size_default)
            self.roiHeight.setValue(self.roi_size_default)
            # self.deleteRoi_button.setEnabled(False)
            # self.addEntry_button.setEnabled(False)
            self.removeEntry_button.setEnabled(False)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            # self.deleteRoi_button.setEnabled(True)
            # self.addEntry_button.setEnabled(True)
            self.removeEntry_button.setEnabled(True)

            item = selected[0]
            self.roiName_input.setText(item.name)
            # print('before', item.width, item.height)
            w = item.width
            h = item.height
            self.roiWidth.setValue(w)
            # print('between', item.width, item.height)
            self.roiHeight.setValue(h)
            # print('after', item.width, item.height)
            self.xCoords.setText(f"{item.pos().x():.2f}")
            self.yCoords.setText(f"{item.pos().y():.2f}")

            # if item.inTable:
            row = item.model_index.row()
            self.tableView.selectRow(row)

    def addRoiItem(self):
        new_rect = RoiItem(0, 0, self.roi_size_default, self.roi_size_default, self.pixmapItem)
        self.roi_list.append(new_rect)

        name = QStandardItem(self.roiName_input.text())
        coords = QStandardItem("0, 0")
        dims = QStandardItem(f"{self.roi_size_default} x {self.roi_size_default}")
        self.tableModel.appendRow([name, coords, dims])
        new_rect.model_index = QPersistentModelIndex(coords.index())
        self.tableView.selectRow(self.tableModel.rowCount()-1)

    def setRoiName(self):
        selected = self.scene.selectedItems()
        if not selected:
            return
        item = selected[0]
        item.name = self.roiName_input.text()
        self.update_entry(item)

    def removeRoi(self):
        selected = self.scene.selectedItems()
        try:
            roi_item = selected[0]
            # if roi_item.inTable:
            item_match = self.tableModel.findItems(roi_item.name, Qt.MatchFlag.MatchFixedString, 0)
            row = item_match[0].row()
            taken_row = self.tableModel.takeRow(row)
            del taken_row
                # roi_item.inTable = False
            self.scene.removeItem(roi_item)
            self.roi_list.remove(roi_item)
        except IndexError:
            return

    def clearTable(self):
        self.tableModel.clear()
        self.tableModel.setHorizontalHeaderItem(0, QStandardItem('Name'))
        self.tableModel.setHorizontalHeaderItem(1, QStandardItem('Coordinates'))
        self.tableModel.setHorizontalHeaderItem(2, QStandardItem('Size (w x h)'))
        # for roi_item in self.roi_list:
        #     roi_item.inTable = False

    def autoExtract(self):
        # self.raw_image
        gray_image = cv2.cvtColor(self.raw_image, cv2.COLOR_BGR2GRAY)
        ret, thresh1 = cv2.threshold(gray_image, 180, 255,cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(thresh1, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        
        idx = 1
        for ctr in contours:
            # (x, y), radius = cv2.minEnclosingCircle(ctr)
            x, y, w, h = cv2.boundingRect(ctr)
            if w*h > 100:
                name = f"LED{idx}"
                new_roi = RoiItem(0, 0, int(w), int(h), self.pixmapItem)
                new_roi.setPos(int(x), int(y))
                self.roi_list.append(new_roi)
                idx += 1

                name_item = QStandardItem(name)
                coords_item = QStandardItem(f"{x:.2f}, {y:.2f}")
                dims_item = QStandardItem(f"{int(w)} x {int(h)}")
                self.tableModel.appendRow([name_item, coords_item, dims_item])
                new_roi.name = name
                # new_roi.inTable = True
                new_roi.model_index = QPersistentModelIndex(coords_item.index())

    def exportTable(self):
        now = datetime.now()
        if not self.orig_source:
            self.orig_source = 'soniquemini_template.txt'
        os.rename(self.orig_source, f'_template_{now.strftime(r"%y%m%d_%H%M%S")}.txt')

        with open(self.orig_source, 'w') as file:
            # Include header so we know what the dimensions are for
            file.write('Name,Left,Top,Width,Height\n')
            for row in range(self.tableModel.rowCount()):
                roi_name = self.tableModel.item(row, 0).text()
                roi_coords = self.tableModel.item(row, 1).text().split(',')
                x = round(float(roi_coords[0].strip()))
                y = round(float(roi_coords[1].strip()))
                dims = self.tableModel.item(row, 2).text().split('x')
                w = int(dims[0].strip())
                h = int(dims[1].strip())

                # print(roi_name, x, y, w, h)
                file.write(f"{roi_name},{x},{y},{w},{h}\n")

        self.close()

    def importTable(self, file_source):
        self.orig_source = file_source
        with open(file_source, 'r') as old_roi:
            # old_roi.readline() # skip header
            for line in old_roi:
                matches = re.findall(r'\d+', line)
                if not matches:
                    continue

                try:
                    name, x, y, width, height = line.split(',')
                except ValueError:
                    name, x, y = line.split(',')
                    width = 120
                    height = 120
                new_roi = RoiItem(0, 0, int(width), int(height), self.pixmapItem)
                new_roi.setPos(float(x), float(y))
                # new_roi.update_pen('green')
                self.roi_list.append(new_roi)

                name_item = QStandardItem(name)
                coords_item = QStandardItem(f"{float(x)}, {float(y)}")
                dims_item = QStandardItem(f"{width} x {height}")
                self.tableModel.appendRow([name_item, coords_item, dims_item])
                new_roi.name = name
                # new_roi.inTable = True
                new_roi.model_index = QPersistentModelIndex(coords_item.index())

    def updateZoom(self, value):
        old_value = int(self.zoomCtrl_text.text().strip('%'))
        self.zoomCtrl_text.setText(f"{value}%")
        scaleFactor = round(value / old_value, 4)
        if value == 100:
            rect = self.scene.sceneRect()
            self.graphicsView.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.graphicsView.scale(scaleFactor, scaleFactor)

    def resetZoom(self):
        self.zoomCtrl_text.setText("100%")
        self.zoomCtrl_slider.setValue(100)
        rect = self.scene.sceneRect()
        self.graphicsView.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
    
    def updateWidth(self, value):
        selected = self.scene.selectedItems()
        if not selected:
            return
        roi_item = selected[0]
        if self.squareMode.isChecked():
            self.roiHeight.setValue(value)
        roi_item.resizeWidth(value)
        self.update_entry(roi_item)

    def updateHeight(self, value):
        selected = self.scene.selectedItems()
        if not selected:
            return
        roi_item = selected[0]
        roi_item.resizeHeight(value)
        self.update_entry(roi_item)

    def on_square(self, state):
        if self.squareMode.isChecked():
            self.roiWidth.valueChanged.connect(self.updateHeight)
            self.roiHeight.setEnabled(False)
        else:
            self.roiWidth.valueChanged.disconnect(self.updateHeight)
            self.roiHeight.setEnabled(True)

    def closeEvent(self, event):
        if self.cam_params != self.orig_params:
            self.new_cam_params.emit(self.cam_params)
        self.vthread.quit()
        event.accept()

# def helper():
#     with open('template_coordinates_old2.csv', 'r') as file:
#         final = [0 for _ in range(80)]
#         for f in file:
#             name, x, y = f.split(',')
#             num = int(name[3:]) - 1
#             x = round((int(x) * 0.94) + 220)
#             y = round((int(y) * 0.94) - 30)
#             final[num] = (x,y)

#     with open('template_coordinates.csv', 'w') as file:
#         for i, fin in enumerate(final, 1):
#             # print(f"LED{i},{fin[0]},{fin[1]}")
#             file.write(f"LED{i},{fin[0]},{fin[1]}\n")

def nothing(e):
    pass

def dummy():
    cv2.namedWindow('test')
    cv2.createTrackbar('focus', 'test', 0, 50, nothing) # 6 == 35
    cv2.createTrackbar('zoom', 'test', 0, 300, nothing) # 300 == 400
    cv2.createTrackbar('exposure', 'test', 15, 15, nothing) # 9 == -6

    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    while True:
        focus = (cv2.getTrackbarPos('focus', 'test') + 1) * 5
        cap.set(cv2.CAP_PROP_FOCUS, focus)
        zoom = cv2.getTrackbarPos('zoom', 'test') + 100
        cap.set(cv2.CAP_PROP_ZOOM, zoom)
        expo = cv2.getTrackbarPos('exposure', 'test') - 15
        cap.set(cv2.CAP_PROP_EXPOSURE, expo)

        ret, frame = cap.read()
        frame = cv2.resize(frame, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        cv2.imshow('test', frame)

        if cv2.waitKey(1) == ord('q'):
            break

def click_event(event, x, y, flags, params):
    if event == cv2.EVENT_LBUTTONDOWN:
        print(x, y)

if __name__=='__main__':
    app = QApplication(sys.argv)
    app.setStyle('windowsvista')


    cap = cv2.VideoCapture(1, cv2.CAP_DSHOW)
    # cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    # cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
    for i in range(10):
        ret, frame = cap.read()
    cap.release()

    debug = DebugROI()
    debug.setImage(frame)
    debug.show()

    # img_loader = ImageEditor()
    # img_loader.show()

    sys.exit(app.exec())