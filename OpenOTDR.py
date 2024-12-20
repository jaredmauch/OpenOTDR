#!/usr/bin/env python3

import sys
import os
import json
from collections import deque
from threading import Lock
from PyQt6 import QtWidgets
from PyQt6.QtWidgets import QFileDialog
from PyQt6 import QtPrintSupport
from PyQt6 import QtGui
from PyQt6 import QtCore
#from PyQt6 import Qt
import PyQt6
import PyQt6.QtCore
import numpy as np
from scipy.ndimage import zoom
from scipy.signal import find_peaks
#from pyotdr import sorparse
import otdrparser
from matplotlib.figure import Figure
from matplotlib import cm
from matplotlib import colors
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.widgets import Cursor

import mainwindow


'''An Open Source OTDR reporting tool'''


def round_sig(value, significant_figures):
    '''Rounds a value to a number of significant figures.
    However the value is less than 1 then simply round it to 1 D.P.'''
    if value < 1:
        return round(value, 1)
    return round(value, -int(np.floor(np.sign(value) * np.log10(abs(value)))) + significant_figures)


def _low_pass_filter_trace(a_raw_trace, window_len):
    '''A simple LowPass Hanning filter'''
    samples = np.r_[a_raw_trace[0][window_len-1:0:-1],
                    a_raw_trace[0],
                    a_raw_trace[0][-2:-window_len-1:-1]]
    window = np.hanning(window_len)
    a_smoothed_levels = np.convolve(window/window.sum(), samples, mode='valid')
    trim = min(len(a_smoothed_levels), len(a_raw_trace[1]))
    a_trace = np.array([a_smoothed_levels[:trim], a_raw_trace[1][:trim]])
    return a_trace


def prepare_data(self, window_len):
    '''Transforms the trace data to unify sample width and signal quality'''
    #a_raw_trace = d_data["trace"]
    # Smoothing
    #a_smooth_trace = _low_pass_filter_trace(a_raw_trace, window_len)
    # Scale to ensure resolution per distance unit is equal.
    #a_trace = zoom(a_smooth_trace, zoom=(1.0, d_data["meta"]["FxdParams"]["resolution"]), order=1)
    self.meta_model.clear()
    self.meta_model.setHorizontalHeaderLabels(['Name', 'Value'])

    raw_data = {}
    raw_data[0] = []
    raw_data[1] = []
    for kind in self.d_meta:
        if kind.get('name', None) == 'DataPts':
            data_pts = kind.get('data_points', None)
            for dp in data_pts:
                raw_data[0].append(dp[0])
                raw_data[1].append(dp[1])

        if kind.get('name', None) in ('GenParams', 'SupParams', 'FxdParams'):
#            print(kind)
            for k in kind:
#                print("k=", k)
                if k == 'name':
                    continue
                current_row = self.meta_model.rowCount()
                self.meta_model.insertRow(current_row)
                value_text = QtGui.QStandardItem()
                value_text.setText(str(k))
                value_text.setEditable(False)
                self.meta_model.setItem(current_row, 0, value_text)
#
                value_text = QtGui.QStandardItem()
                value_text.setText(str(kind.get(k, None)))
                value_text.setEditable(False)
                self.meta_model.setItem(current_row, 1, value_text)

    return raw_data


def differentiate_data(d_data):
    '''Calculates the 1st order differential of the data'''
    a_raw_trace = d_data
    a_diff_trace = np.diff(a_raw_trace[0])
    a_clean_trace = []
    for sample_index in range(len(a_raw_trace[0])):
        if sample_index < len(a_diff_trace)-1:
            a_clean_trace.append(a_diff_trace[sample_index])
        else:
            a_clean_trace.append(0)
    return [a_clean_trace, a_raw_trace]


def find_edges(a_differential_trace):
    '''Finds windows that contain features'''
    a_abs_trace = [abs(sample) for sample in a_differential_trace[0]]
    a_peaks = find_peaks(a_abs_trace, 0.00125, width=5, distance=150)
    return [a_peaks[0],
            [a_differential_trace[1][0][peak] for peak in a_peaks[0]],
            [a_differential_trace[1][1][peak] for peak in a_peaks[0]]]


def wavelength_to_rgb(s_wavelength):
    '''Convert the wavelength to a spectral 'false' colour'''
    wavelength = int(s_wavelength[:-3])
    norm = colors.Normalize(vmin=1250, vmax=1650, clip=True)
    mapper = cm.ScalarMappable(norm=norm, cmap=cm.jet_r)
    red, green, blue, _ = mapper.to_rgba(wavelength)
    return "#{:02X}{:02X}{:02X}".format(int(red*255), int(green*255), int(blue*255))


class CustomNavigationToolbar(NavigationToolbar):
    '''Removing a couple of irrelavent tools from the toolbar'''
    toolitems = (('Home', 'Reset original view', 'home', 'home'),
                 ('Back', 'Back to previous view', 'back', 'back'),
                 ('Forward', 'Forward to next view', 'forward', 'forward'),
                 (None, None, None, None),
                 ('Pan', 'Pan axes with left mouse, zoom with right', 'move', 'pan'),
                 ('Zoom', 'Zoom to rectangle', 'zoom_to_rect', 'zoom'))


class NaturalSortFilterProxyModel(QtCore.QSortFilterProxyModel):
    '''Filter as a human would, not alphanumeric'''
    @staticmethod
    def _numeric_key(key):
        '''The numeric key'''
        if key:
            return float(key)
        return float('-inf')

    def lessThan(self, left, right):
        '''The < operator for Qt5'''
        left_data = self.sourceModel().data(left)
        right_data = self.sourceModel().data(right)
        return self._numeric_key(left_data) < self._numeric_key(right_data)


class MainWindow(QtWidgets.QMainWindow):
    '''The main window handler class'''
    def __init__(self):
        super(MainWindow, self).__init__()
        self.user_interface = mainwindow.Ui_MainWindow()
        self.user_interface.setupUi(self)
        self.project_model = QtGui.QStandardItemModel()
        self.user_interface.treeView.setModel(self.project_model)
        # trace event processing model
        self.events_model = QtGui.QStandardItemModel()
        #
        self.events_proxy_model = NaturalSortFilterProxyModel()
        self.events_proxy_model.setSourceModel(self.events_model)
#        self.events_proxy_model.sort(1, PyQt6.QtCore.Qt.SortOrder.AscendingOrder)
        self.user_interface.eventTableView.setModel(self.events_proxy_model)
        self.user_interface.eventTableView.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
#
        # meta processing model
        self.meta_model = QtGui.QStandardItemModel()
#
        self.meta_proxy_model = NaturalSortFilterProxyModel()
        self.meta_proxy_model.setSourceModel(self.meta_model)
#        self.meta_proxy_model.sort(1, PyQt6.QtCore.Qt.SortOrder.AscendingOrder)
        self.user_interface.metaTableView.setModel(self.meta_proxy_model)
        self.user_interface.metaTableView.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        #
        self.user_interface.openProject.clicked.connect(self.open_project)
        self.user_interface.saveProject.clicked.connect(self.save_project)
        self.user_interface.printReport.clicked.connect(self.print_pdf)
        self.user_interface.printReport.setDisabled(False)
        self.user_interface.addTrace.clicked.connect(self.add_trace)
        self.user_interface.removeTrace.clicked.connect(self.remove_trace)
        self.user_interface.recalculateEvents.clicked.connect(self.recalculate_events)
        self.window_len = 0
        self.canvas = None
        self.d_meta = None
        self.plt = None
        self.cursor = None
        self.toolbar = None
        self.raw_features = []
        self.raw_traces = []
        self.files = {}
        self.meta = {}
        self.busy = Lock()
        self._draw()

    def __preprocess_data(self, d_meta, l_raw_trace):
        '''Convert the raw data into a numpy array'''
        sample_spacing = float(d_meta["FxdParams"]["sample spacing"][:-5])
        self.window_len = int(0.5/sample_spacing)
        q_trace = deque(l_raw_trace)
        l_distance = list()
        l_level = list()
        raw_row = q_trace.popleft()
        while q_trace:
#            print("raw_row=", raw_row)
            raw_distance, raw_level = raw_row.replace("\n", "").split("\t")
            f_distance = float(raw_distance)
            f_level = float(raw_level)
            l_distance.append(f_distance)
            l_level.append(f_level)
            raw_row = q_trace.popleft()
        a_trace = np.array([l_level, l_distance])
        return a_trace

    def _load_file(self, url, _project=False):
        '''Load the raw SOR file from provided url into the internal data format'''
        with open(url, 'rb') as fp:
            d_meta = otdrparser.parse(fp)
            self.d_meta = d_meta
            data_pts = None
            for row in d_meta:
                if row.get('name', None) == "DataPts":
                    data_pts = row
            self.files[url] = {"meta": d_meta, "raw_trace": data_pts}
#        print("d_meta=", json.dumps(d_meta, sort_keys=True, indent=4))
#        print("l_raw_trace=", json.dumps(l_raw_trace, sort_keys=True, indent=4))
#        a_trace = self.__preprocess_data(d_meta, l_raw_trace)
        d_data= prepare_data(self, self.window_len)
        filename = os.path.basename(url)
        item = QtGui.QStandardItem(filename)
        item.data = d_data
        self.project_model.appendRow(item)

    def hover(event, graph_info):
#        print("hover event:", dir(event))
# hover graph_info: ['__class__', '__delattr__', '__dict__', '__dir__', '__doc__', '__eq__', '__format__', '__ge__', '__getattribute__', '__getstate__', '__gt__', '__hash__', '__init__', '__init_subclass__', '__le__', '__lt__', '__module__', '__ne__', '__new__', '__reduce__', '__reduce_ex__', '__repr__', '__setattr__', '__sizeof__', '__str__', '__subclasshook__', '__weakref__', '_guiEvent', '_guiEvent_deleted', '_last_axes_ref', '_lastevent', '_process', '_set_inaxes', 'button', 'canvas', 'dblclick', 'guiEvent', 'inaxes', 'key', 'lastevent', 'modifiers', 'name', 'step', 'x', 'xdata', 'y', 'ydata']

        print("hover graph_info:", dir(graph_info))
        print("graph_info.guiEvent:", graph_info.guiEvent)
        print("graph_info.inaxes:", graph_info.inaxes)
        print("graph_info.key:", graph_info.key)
        print("graph_info.x:", graph_info.x)
        print("graph_info.y:", graph_info.y)
        print("graph_info.xdata(km):", graph_info.xdata)
        print("grpah_info.xdata(feet):", graph_info.xdata * 3280.8399)
        print("graph_info.ydata:", graph_info.ydata)
        print("graph_info.name:", graph_info.name)

        if graph_info.inaxes == graph_info.ax:
            cont, ind = line.contains(event)
            if cont:
                update_annot(ind)
                annot.set_visible(True)
                fig.canvas.draw_idle()
            else:
                if vis:
                    annot.set_visible(False)
                    fig.canvas.draw_idle()


    def on_draw(self, event):
        print("on_draw:", event)
#        self.cursor = Cursor(self.plt, horizOn=True, vertOn=True, useblit=True, color='red', linewidth=2)

    def button_press(event, graph_info):
#        print("button_press event:", dir(event))
# button_press graph_info: ['__class__', '__delattr__', '__dict__', '__dir__', '__doc__', '__eq__', '__format__', '__ge__', '__getattribute__', '__getstate__', '__gt__', '__hash__', '__init__', '__init_subclass__', '__le__', '__lt__', '__module__', '__ne__', '__new__', '__reduce__', '__reduce_ex__', '__repr__', '__setattr__', '__sizeof__', '__str__', '__subclasshook__', '__weakref__', '_guiEvent', '_guiEvent_deleted', '_last_axes_ref', '_lastevent', '_process', '_set_inaxes', 'button', 'canvas', 'dblclick', 'guiEvent', 'inaxes', 'key', 'lastevent', 'modifiers', 'name', 'step', 'x', 'xdata', 'y', 'ydata']

#        print("button_press graph_info:", dir(graph_info))
        print("graph_info.guiEvent:", graph_info.guiEvent)
        print("graph_info.inaxes:", graph_info.inaxes)
        print("graph_info.key:", graph_info.key)
        print("graph_info.button:", graph_info.button) # (1 left, 2 middle, 3 right)
        print("graph_info.dblclick:", graph_info.dblclick) # doubleclick?
#        print("graph_info.x:", graph_info.x)
#        print("graph_info.y:", graph_info.y)
        print("graph_info.xdata(km):", graph_info.xdata)
        print("grpah_info.xdata(feet):", graph_info.xdata * 3280.8399)

        print("graph_info.ydata:", graph_info.ydata)
        print("graph_info.name:", graph_info.name)


    def _draw(self):
        '''(re)draw the plot with the latest data'''
        fig = Figure()
        self.plt = fig.add_subplot(1, 1, 1)
        if self.raw_traces:
#            print("self.raw_traces:", self.raw_traces)
            for d_final_data in self.raw_traces:
                wavelength = 1310
                print("d_final_data", d_final_data)
#                print("wavelength=", wavelength)
                self.plt.plot(d_final_data[0],
                         d_final_data[1],
                         label=wavelength,
                         color=wavelength_to_rgb(str(wavelength)))
#            self.plt.set_xlim([0, None])

        if self.canvas:
            self.user_interface.graphLayout.removeWidget(self.canvas)
            self.canvas.close()
        if self.toolbar:
            self.user_interface.graphLayout.removeWidget(self.toolbar)
            self.toolbar.close()
        fig.legend()
# if i want to have my own hover function
#        fig.canvas.mpl_connect("motion_notify_event", self.hover)
        fig.canvas.mpl_connect("button_press_event", self.button_press)
#        fig.canvas.mpl_connect("scroll_event", self.zoom)
        fig.canvas.mpl_connect('draw_event', self.on_draw)

        self.canvas = FigureCanvas(fig)
#        self.toolbar = CustomNavigationToolbar(self.canvas, self, coordinates=True)
        self.toolbar = NavigationToolbar(self.canvas, self, coordinates=True)
        self.cursor = Cursor(self.plt, horizOn=True, vertOn=True, useblit=True, color='red', linewidth=2)
        self.user_interface.graphLayout.addWidget(self.canvas)
        self.user_interface.graphLayout.addWidget(self.toolbar)
        self.recalculate_events()


    def open_project(self):
        '''Load a project from a file'''
        if self.busy.locked():
            return
        with self.busy:
            dialog = QtWidgets.QFileDialog(self)
            dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
            uri, _ = dialog.getOpenFileName(self, "Open project", "", "OpenOTDR Project Files(*.opro);;All Files (*)")
            if uri:
                with open(uri, "r") as file:
                    content = json.load(file)
                self.meta = content["meta"]
                for uri, data in content["files"].items():
                    self._load_file(uri, _project=data)
                    self._draw()
                for index in range(self.project_model.rowCount()):
                    raw_data = self.project_model.item(index).data
                    self.raw_traces.append(raw_data)
                    self._draw()
                self._draw()
        self.recalculate_events()
        self._draw

    def save_project(self):
        '''Save a project to a file'''
        if self.busy.locked():
            return
        with self.busy:
            dialog = QtWidgets.QFileDialog(self)
            dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
            uri, _ = dialog.getSaveFileName(self, "Save project", "", "OpenOTDR Project Files(*.opro);;All Files (*)")
            if uri:
                _, extension = os.path.splitext(uri)
                if not extension:
                    uri += ".opro"
                content = {"meta": self.meta, "files": self.files}
                with open(uri, "w") as file:
                    json.dump(content, file)

    def print_pdf(self):
        '''Print the report to pdf'''
        if self.busy.locked():
            return
        with self.busy:
            printer = QtPrintSupport.QPrinter()
            dialog = QtPrintSupport.QPrintDialog(printer, self)
            dialog.setModal(True)
            dialog.setWindowTitle("Print Document")
            dialog.options = (QtPrintSupport.QAbstractPrintDialog.PrintToFile
                              | QtPrintSupport.QAbstractPrintDialog.PrintShowPageSize
                              | QtPrintSupport.QAbstractPrintDialog.PrintPageRange)
            if dialog.exec_():
                print("printing")
                # TODO Printing

    def add_trace(self):
        '''Load a new trace'''
        if self.busy.locked():
            return
        with self.busy:
            dialog = QtWidgets.QFileDialog(self)
            dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
            files, _ = dialog.getOpenFileNames(self, "Add traces", "", "OTDR Trace Files(*.sor);;All Files (*)")
            if not files:
                return
            for filename in files:
                self._load_file(filename)
            l_traces = []
            l_feature_points = []
            for index in range(self.project_model.rowCount()):
                raw_data = self.project_model.item(index).data
                self.raw_traces.append(raw_data)
                d_data = prepare_data(self, self.window_len)
                l_traces.append(d_data)
                l_feature_points.append(find_edges(differentiate_data(d_data)))
            raw_features = l_feature_points
            raw_traces = l_traces
#            print("add_trace: raw_features:", raw_features)
#            if not raw_features:
#                return
            d_events = self._filter_events(raw_features)
            self._update_events_table(d_events, raw_traces)
            self._draw()

    def remove_trace(self):
        '''Remove a trace'''
        if self.busy.locked():
            return
        with self.busy:
            if not self.user_interface.treeView.selectedIndexes():
                return
            indexes = self.user_interface.treeView.selectedIndexes()
            for index in indexes:
                self.project_model.removeRow(index.row())
                del self.raw_traces[index.row()]
            self._draw()

    @staticmethod
    def _filter_events(raw_features):
        '''Filter the detected features of each trace to make a single set with no duplicates or ghosts'''
        d_events = {}
        for trace_features in raw_features:
            for index, feature in enumerate(trace_features[2]):
                feature_position = round_sig(feature, 3)
                if feature_position not in d_events and feature_position-0.1 not in d_events and feature_position+0.1 not in d_events:
                    d_events[feature_position] = {
                        "indexes": []
                        }
                if feature_position in d_events:
                    d_events[feature_position]["indexes"].append(trace_features[0][index])
                elif feature_position-0.1 in d_events:
                    d_events[feature_position-0.1]["indexes"].append(trace_features[0][index])
                elif feature_position+0.1 in d_events:
                    d_events[feature_position+0.1]["indexes"].append(trace_features[0][index])
        return d_events

    def __calculate_loss_and_dispersion(self, raw_traces, meta_data):
        '''Calculate the loss and dispersion of an event'''
        start_values = []
        end_values = []
        for trace in raw_traces:
            for i in meta_data["indexes"]:
                start_index = max(0, i-self.window_len)
                start_values.append(trace["trace"][0][start_index])
                end_index = min(len(trace["trace"][0])-1, i+self.window_len)
                end_values.append(trace["trace"][0][end_index])
                self.canvas.figure.get_axes()[0].axvspan(trace["trace"][1][start_index], trace["trace"][1][end_index], color='yellow', alpha=0.5)
        average_start = sum(start_values)/len(start_values)
        average_end = sum(end_values)/len(end_values)
        loss = average_start - average_end
        difference_start = max(start_values) - min(start_values)
        difference_end = max(end_values) - min(end_values)
        #print("difference_end=", difference_end)
        #print("difference_start=", difference_start)
        if difference_start == 0 or difference_end == 0:
            dispersion_factor = 0
        else:
            dispersion_factor = round(max(difference_end/difference_start, difference_start/difference_end))
        return loss, dispersion_factor

    def _update_events_table(self, d_events, raw_traces):
        '''Update the events table in the UI'''
        print("_update_events_table")
        self.events_model.clear()
        key_events = None
        for row in self.d_meta:
            if row.get('name', None) == 'KeyEvents':
                key_events = row
#        print("update_events_table: key_events:", key_events)
        num_events = 0
        self.events_model.setHorizontalHeaderLabels(['comment', 'dist(km)', 'dist(ft)', 'peak', 'refl loss', 'slope', 'splice_loss', 'type'])
        if key_events is not None:
            number_of_events = int(key_events.get('number_of_events', 0))
            print("number_of_events:", number_of_events)

            for event in key_events.get('events', []):
                current_row = self.events_model.rowCount()
                self.events_model.insertRow(current_row)

                event_comment = QtGui.QStandardItem()
                event_comment.setText(str(event.get('comment')))
                event_comment.setEditable(False)
                self.events_model.setItem(current_row, 0, event_comment)

                event_position_km = QtGui.QStandardItem()
                event_position_km.setText(str(event.get('distance_of_travel', 0)))
                event_position_km.setEditable(False)
                self.events_model.setItem(current_row, 1, event_position_km)

                event_position_ft = QtGui.QStandardItem()
                event_position_ft.setEditable(False)
                event_position_ft.setText(str(float(event.get('distance_of_travel', 0)) * 3280.8399))
                self.events_model.setItem(current_row, 2, event_position_ft)

                event_peak = QtGui.QStandardItem()
                event_peak.setText(str(event.get('peak_point')))
                event_peak.setEditable(False)
                self.events_model.setItem(current_row, 3, event_peak)

                event_loss = QtGui.QStandardItem()
                event_loss.setText(str(event.get('reflection_loss')))
                event_loss.setEditable(False)
                self.events_model.setItem(current_row, 4, event_loss)

                event_slope = QtGui.QStandardItem()
                event_slope.setText(str(event.get('slope')))
                event_slope.setEditable(False)
                self.events_model.setItem(current_row, 5, event_slope)

                event_splice_loss = QtGui.QStandardItem()
                event_splice_loss.setText(str(event.get('splice_loss')))
                event_splice_loss.setEditable(False)
                self.events_model.setItem(current_row, 6, event_splice_loss)

                event_type = QtGui.QStandardItem()
                event_type.setText(str(event.get('event_type_details')['event']))
                event_type.setEditable(False)
                self.events_model.setItem(current_row, 7, event_type)

##        self.events_model.setHorizontalHeaderLabels(['Event',
##                                                     'Dist (km)',
##                                                     'Loss (dB)',
##                                                     'Dispersion factor', 'Dist ft'])
###
##
##        #
##        for position, meta_data in d_events.items():
##            current_row = self.events_model.rowCount()
##            self.events_model.insertRow(current_row)
##            event_position_km = QtGui.QStandardItem()
##            event_position_km.setText(str(position))
##            event_position_km.setEditable(False)
##            self.events_model.setItem(current_row, 1, event_position_km)
##
##            event_position_ft = QtGui.QStandardItem()
##            event_position_ft.setEditable(False)
##            event_position_ft.setText(str(position * 3280.8399))
##            self.events_model.setItem(current_row, 4, event_position_ft)
##
##            loss, dispersion_factor = self.__calculate_loss_and_dispersion(raw_traces, meta_data)
##
##            event_loss = QtGui.QStandardItem()
##            event_loss.setText(str(loss))
##            event_loss.setEditable(False)
##            self.events_model.setItem(current_row, 2, event_loss)
##
##            event_dispersion = QtGui.QStandardItem()
##            event_dispersion.setText(str(dispersion_factor))
##            event_dispersion.setEditable(False)
##            self.events_model.setItem(current_row, 3, event_dispersion)
##
##            # pull in event type from the trace ideally
##            event_type = QtGui.QStandardItem()
##            event_type.setEditable(True)
##            self.events_model.setItem(current_row, 0, event_type)
##
        self.events_model.sort(1)
        self.user_interface.eventTableView.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)


    def recalculate_events(self):
        '''Recalculate the events'''
        print("starting recalculate_events()")
        if self.busy.locked():
            print("recalculate_events self.busy.locked()")
            return
        with self.busy:
            l_traces = []
            l_feature_points = []
            for index in range(self.project_model.rowCount()):
                raw_data = self.project_model.item(index).data
                d_data = prepare_data(self, self.window_len)
                l_traces.append(d_data)
                l_feature_points.append(find_edges(differentiate_data(d_data)))
            raw_features = l_feature_points
            raw_traces = l_traces
            print("recalculate_events: raw_features:", raw_features)
            if not raw_features:
                return
            d_events = self._filter_events(raw_features)
            self._update_events_table(d_events, d_data)


APP = QtWidgets.QApplication(sys.argv)

MAIN_WINDOW = MainWindow()
MAIN_WINDOW.setWindowTitle("OpenOTDR")
MAIN_WINDOW.show()

sys.exit(APP.exec())
