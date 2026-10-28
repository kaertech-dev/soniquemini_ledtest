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

    def __init__(self, source=0):
        super().__init__()
        self._run_flag = True
        self.source = source

        self.cap = cv2.VideoCapture(self.source, cv2.CAP_DSHOW)
        # self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        # self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 960)

    def read_cam_prop(self):
        br = self.cap.get(cv2.CAP_PROP_BRIGHTNESS)
        con = self.cap.get(cv2.CAP_PROP_CONTRAST)
        exp = self.cap.get(cv2.CAP_PROP_EXPOSURE)
        zoom = self.cap.get(cv2.CAP_PROP_ZOOM)
        foc = self.cap.get(cv2.CAP_PROP_FOCUS)

        print('values', br, con, exp, foc, zoom)
        return br, con, exp, foc, zoom

    def set_cam_prop(self, br=128.0, con=128.0, exp=-5, foc=1, zoom=100):
        self.brightness = br
        self.cam_exposure = exp
        self.contrast = con
        self.focus = foc
        self.zoom = zoom
        # print('before', br, con, exp, foc, zoom)

    def grab_frame(self):
        ret, frame = self.cap.read()
        return frame

    def run(self):
        br, con, exp, foc, zoom = self.read_cam_prop()
        self.set_cam_prop(br, con, exp, foc, zoom)

        while self._run_flag:
            self.cap.set(cv2.CAP_PROP_BRIGHTNESS, self.brightness)
            self.cap.set(cv2.CAP_PROP_CONTRAST, self.contrast)
            self.cap.set(cv2.CAP_PROP_EXPOSURE, self.cam_exposure)
            self.cap.set(cv2.CAP_PROP_FOCUS, self.focus)
            self.cap.set(cv2.CAP_PROP_ZOOM, self.zoom)
            ret, frame = self.cap.read()
            # if ret:
            #     adjusted = cv2.convertScaleAbs(frame, alpha=self.contrast, beta=self.brightness)
                # cv2.imwrite('image.jpg', adjusted)
            if ret:
                self.change_pixmap_signal.emit(frame)
        # self.cap.release()

    def stop(self):
        self._run_flag = False
        self.wait()

class ImageEditor(QDialog):
    def __init__(self):
        super().__init__()
        uic.loadUi(get_path('layout/image_editor.ui'), self)

        self.scene = QGraphicsScene(0,0,1920,1080)
        self.graphicsView.setScene(self.scene)
        rect = self.scene.sceneRect()
        self.graphicsView.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

        self.pixmapItem = QGraphicsPixmapItem()
        self.scene.addItem(self.pixmapItem)

        self.vthread = VideoThread(1)
        # self.vthread.change_pixmap_signal.connect(self.update_image)
        br, con, exp, foc, zoom = self.vthread.read_cam_prop()
        self.camera_config = {'brightness': br, 'contrast': con, 'exposure': exp, 'focus': foc, 'zoom': zoom}
        self.init_spin_box(br, con, exp, foc, zoom)
        self.raw_image = None
        self.final_pixmap = None

        # self.open_file_button.clicked.connect(self.open_file_dialog)
        # self.open_cam_button.clicked.connect(self.open_camera)
        self.overlay_button.clicked.connect(self.importTable)
        self.save_exit_button.clicked.connect(self.save_and_exit)

        self.brightness_input.valueChanged.connect(self.update_img_params)
        self.contrast_input.valueChanged.connect(self.update_img_params)
        self.exposure_input.valueChanged.connect(self.update_img_params)
        self.focus_input.valueChanged.connect(self.update_img_params)
        self.zoom_input.valueChanged.connect(self.update_img_params)

    def init_spin_box(self, br, con, ex, foc, zm):
        self.brightness_input.setValue(int(br))
        self.contrast_input.setValue(int(con))
        self.exposure_input.setValue(int(ex))
        self.focus_input.setValue(int(foc))
        self.zoom_input.setValue(int(zm))

    def update_image(self, cv_img):
        self.raw_image = cv_img
        h, w, ch = cv_img.shape
        bytes_per_line = ch * w
        qt_img = QImage(cv_img.data, w, h, bytes_per_line, QImage.Format.Format_BGR888)
        self.pixmapItem.setPixmap(QPixmap.fromImage(qt_img))
        # self.scene.setSceneRect(0,0,w,h)
        rect = self.scene.itemsBoundingRect()
        self.graphicsView.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def update_img_params(self, value):
        br = self.brightness_input.value()
        con = self.contrast_input.value()
        exp = self.exposure_input.value()
        foc = self.focus_input.value()
        zm = self.zoom_input.value()
        
        # print('cam', br, con, exp)
        self.camera_config = {'brightness': br, 'contrast': con, 'exposure': exp, 'focus': foc, 'zoom': zm}
        self.vthread.set_cam_prop(br, con, exp, foc, zm)
        # self.vthread.brightness = br
        # self.vthread.contrast = con
        # self.vthread.cam_exposure = exp
        # self.vthread.focus = foc
        # self.vthread.zoom = zm

    def save_and_exit(self):
        self.final_pixmap = self.pixmapItem.pixmap()
        cv2.imwrite('test.jpg', self.raw_image)
        self.close()

    def showEvent(self, event):
        if not self.vthread.isRunning():
            self.vthread.start()
        self.vthread.change_pixmap_signal.connect(self.update_image)
        event.accept()

    def closeEvent(self, event):
        # self.vthread.stop()
        self.vthread.change_pixmap_signal.disconnect(self.update_image)
        event.ignore()
        self.hide()

    def importTable(self):
        children = self.pixmapItem.childItems()
        for child in children:
            child.deleteLater()
        
        file, _ = QFileDialog.getOpenFileName(
            self,
            "Select File",
            r"C:\\Users\\ai\\roi_config",
            "Text (*.txt);; CSV (*.csv)"
            )
        if file:
            file_source = str(Path(file))
            # raw_image = cv2.imread(file)
            raww = self.raw_image.copy()
            roi_list = {}
            with open(file_source, 'r') as old_roi:
                old_roi.readline() # skip header
                for line in old_roi:
                    name, x, y, width, height = line.split(',')
                    roi_list[name] = (int(x), int(y), int(width), int(height))
                    # if self._img_source != 'cam':
                    pt1 = (int(x), int(y))
                    pt2 = (int(x) + int(width), int(y) + int(height))
                    # cv2.rectangle(raww, pt1, pt2, (0,255,255), 2)
                    # number = name.split('LED')[-1]
                    # if int(number) > 40:
                    #     number = 'R' + str(int(number)-40)
                    # else:
                    #     number = 'L' + number
                    # cv2.putText(raww, number, pt1, cv2.FONT_HERSHEY_COMPLEX, 1, (0,255,255), 1)
                    new_roi = RoiItem(0, 0, int(width), int(height), self.pixmapItem)
                    new_roi.setPos(float(x), float(y))

            # cv2.imwrite('roi_loc.jpg', raww)

class DebugROI(QDialog):
    def __init__(self):
        super().__init__()
        uic.loadUi(get_path('layout/debug_roi_compact.ui'), self)
        self.roi_size_default = 120
        self.roi_list: list[RoiItem] = []

        self.scene = QGraphicsScene(0,0,1920,1080)
        self.graphicsView.setScene(self.scene)
        rect = self.scene.sceneRect()
        self.graphicsView.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.scene.mouseMoveEvent = self.mouse_move_override
        self.scene.mousePressEvent = self.mouse_press_override
        self.scene.mouseReleaseEvent = self.mouse_release_override

        self.pixmapItem = QGraphicsPixmapItem()
        self.raw_image = None
        self.scene.addItem(self.pixmapItem)

        self.tableModel = QStandardItemModel()
        self.tableModel.setHorizontalHeaderItem(0, QStandardItem('Name'))
        self.tableModel.setHorizontalHeaderItem(1, QStandardItem('Coordinates'))
        self.tableModel.setHorizontalHeaderItem(2, QStandardItem('Size (w x h)'))
        self.tableView.setModel(self.tableModel)

        self.imgReso_text.setText(f"1920x1080")
        self.orig_source = ''

        self.loadImage_button.clicked.connect(self.launch_img_editor)
        # self.clearViewer_button.clicked.connect(self.clear_graphicsscene)

        self.addRoi_button.clicked.connect(self.addRoiItem)
        self.roiName_input.returnPressed.connect(self.setRoiName)
        # self.deleteRoi_button.clicked.connect(self.deleteRoiItem)
        # self.addEntry_button.clicked.connect(self.enterRoi)
        self.removeEntry_button.clicked.connect(self.removeRoi)
        # self.clearList_button.clicked.connect(self.clearTable)
        self.exportList_button.clicked.connect(self.exportTable)

        self.zoomCtrl_slider.valueChanged.connect(self.updateZoom)
        self.resetZoom_button.clicked.connect(self.resetZoom)

        self.roiWidth.valueChanged.connect(self.updateWidth)
        self.roiHeight.valueChanged.connect(self.updateHeight)

        # self.deleteRoi_button.setEnabled(False)
        # self.addEntry_button.setEnabled(False)
        self.removeEntry_button.setEnabled(False)

        # self.pixmapItem.setPixmap(QPixmap(r"C:\Users\ai\led_detection\input_images\2026-08-07\112502_KLLM52830017.jpg"))
        self.img_editor = ImageEditor()
        self.camera_config = {}

    def showEvent(self, event):
        event.accept()
        self.resetZoom()

    def setImage(self, cv_img):
        if cv_img is not None:
            h, w, ch = cv_img.shape
            bytes_per_line = ch * w
            qt_img = QImage(cv_img.data, w, h, bytes_per_line, QImage.Format.Format_BGR888)
            self.pixmapItem.setPixmap(QPixmap.fromImage(qt_img))
            rect = self.scene.itemsBoundingRect()
            self.graphicsView.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
            return True
        else:
            return False

    def launch_img_editor(self):
        self.img_editor.exec()

        self.camera_config = self.img_editor.camera_config
        pixmap_output = self.img_editor.final_pixmap
        if pixmap_output is not None:
            self.pixmapItem.setPixmap(pixmap_output)
            self.raw_image = self.img_editor.raw_image
            h, w, ch = self.raw_image.shape
            self.scene.setSceneRect(0,0,w,h)
            rect = self.scene.itemsBoundingRect()
            self.graphicsView.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
    
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
        os.rename(self.orig_source, f'roi_{now.strftime(r"%Y-%b-%d_%H-%M-%S")}.txt')

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
        roi_item.resizeWidth(value)
        self.update_entry(roi_item)

    def updateHeight(self, value):
        selected = self.scene.selectedItems()
        if not selected:
            return
        roi_item = selected[0]
        roi_item.resizeHeight(value)
        self.update_entry(roi_item)

    def closeEvent(self, a0):
        self.img_editor.destroy()
        return super().closeEvent(a0)

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