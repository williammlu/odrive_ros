import serial
from serial.serialutil import SerialException

import sys
import time
import logging
import traceback

import odrive
from odrive.enums import *
from odrive.utils import dump_errors

import fibre

default_logger = logging.getLogger(__name__)
default_logger.setLevel(logging.DEBUG)

# create console handler and set level to debug
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)

default_logger.addHandler(ch)

class ODriveFailure(Exception):
    pass

class ODriveInterfaceAPI(object):
    encoder_cpr = 8192
    #engaged = False
    
    def __init__(self, logger=None):
        self.id = None
        # self. engaged = False
        self.right_axis = None
        self.left_axis = None
        self.connected = False
        self._index_searched = False
        self.driver = None
        self.logger = logger if logger else default_logger
                
    def __del__(self):
        self.disconnect()
                    
    def connect(self, port=None, right_axis=0, timeout=30, odrive_id=None):
        """
        Connect by serial numbers

        serial numbers:
            207C37823548

        params:
            port
            right_axis
            timeout
            odrive_id - string id unique to each odroid
        """
        self.id = odrive_id
        if self.driver:
            self.logger.info("Already connected. Disconnecting and reconnecting.")
        try:

            self.driver = odrive.find_any(serial_number=odrive_id, timeout=timeout, logger=self.logger)
            self.axes = (self.driver.axis0, self.driver.axis1)

        except:
            self.logger.error("No ODrive found. Is device powered?")
            return False
            
        # save some parameters for easy access
        self.right_axis = self.driver.axis0 if right_axis == 0 else self.driver.axis1
        self.left_axis  = self.driver.axis1 if right_axis == 0 else self.driver.axis0
        self.encoder_cpr = self.driver.axis0.encoder.config.cpr
        
        self.connected = True
        self.logger.info("Connected to ODrive. Hardware v%d.%d-%d, firmware v%d.%d.%d%s" % (
                        self.driver.hw_version_major, self.driver.hw_version_minor, self.driver.hw_version_variant,
                        self.driver.fw_version_major, self.driver.fw_version_minor, self.driver.fw_version_revision,
                        "-dev" if self.driver.fw_version_unreleased else ""
                        ))
        return True
        
    def disconnect(self):
        self.connected = False
        self.right_axis = None
        self.left_axis = None
        
        self._index_searched = False
        
        if not self.driver:
            self.logger.error("Not connected.")
            return False
        
        try:
            self.release()
        except:
            self.logger.error("Error in timer: " + traceback.format_exc())
            return False
        finally:
            self.driver = None
        return True

    def calibrate(self):
        if not self.driver:
            self.logger.error("Not connected.")
            return False
        
        self.logger.info("Vbus %.2fV" % self.driver.vbus_voltage)
        
        for i, axis in enumerate(self.axes):
            self.logger.info("Calibrating axis %d..." % i)
            axis.requested_state = AXIS_STATE_FULL_CALIBRATION_SEQUENCE
            time.sleep(1)
            while axis.current_state != AXIS_STATE_IDLE:
                time.sleep(0.1)
            if axis.error != 0:
                self.logger.error("Failed calibration with axis error 0x%x, motor error 0x%x" % (axis.error, axis.motor.error))
                return False
                
        return True
        
    def index_search(self, wait=True):
        """
        Finding index, requires full rotation on motor
        """
        if not self.driver:
            self.logger.error("Not connected.")
            return False
            
        if self._index_searched: # must be index_searching or already index_searched
            return False
            
        #self.logger.info("Vbus %.2fV" % self.driver.vbus_voltage)

        for i, axis in enumerate(self.axes):
            self.logger.info("Index search index_search axis %d..." % i)
            axis.requested_state = AXIS_STATE_ENCODER_INDEX_SEARCH
        
        if wait:
            for i, axis in enumerate(self.axes):
                while axis.current_state != AXIS_STATE_IDLE:
                    time.sleep(0.1)
            for i, axis in enumerate(self.axes):
                if axis.error != 0:
                    self.logger.error("Failed index_search with axis error 0x%x, motor error 0x%x" % (axis.error, axis.motor.error))
                    return False
        self._index_searched = True
        return True
        
    def index_searching(self):
        return self.axes[0].current_state == AXIS_STATE_ENCODER_INDEX_SEARCH or self.axes[1].current_state == AXIS_STATE_ENCODER_INDEX_SEARCH
    
    def index_searched(self): #
        return self._index_searched and not self.index_searching()
    
    def engaged(self):
        return self.axes[0].current_state == AXIS_STATE_CLOSED_LOOP_CONTROL or self.axes[1].current_state == AXIS_STATE_CLOSED_LOOP_CONTROL
    
    def idle(self):
        return self.axes[0].current_state == AXIS_STATE_IDLE and self.axes[1].current_state == AXIS_STATE_IDLE
        
    def engage(self):
        """
        Enter into 
        """
        if not self.driver:
            self.logger.error("Not connected.")
            return False


        #self.logger.debug("Setting drive mode.")
        for axis in self.axes:
            axis.controller.vel_setpoint = 0
            axis.controller.pos_setpoint = 0
            axis.controller.current_setpoint = 0
            axis.requested_state = AXIS_STATE_CLOSED_LOOP_CONTROL
            axis.controller.config.control_mode = CTRL_MODE_POSITION_CONTROL
        
        return True
        
        
    def release(self):
        if not self.driver:
            self.logger.error("Not connected.")
            return False
        #self.logger.debug("Releasing.")
        for axis in self.axes: 
            axis.requested_state = AXIS_STATE_IDLE

        return True
    
    def drive_vel(self, left=None, right=None):
        if not self.driver:
            self.logger.error("Not connected.")
            return
        elif not self.engaged():
            self.logger.error("Not engaged")
            return
        try:
            if left is not None:
                self.left_axis.controller.config.control_mode = CTRL_MODE_VELOCITY_CONTROL
                self.left_axis.controller.vel_setpoint = left
            if right is not None:
                self.right_axis.controller.config.control_mode = CTRL_MODE_VELOCITY_CONTROL
                self.right_axis.controller.vel_setpoint = right
        except (fibre.protocol.ChannelBrokenException, AttributeError) as e:
           raise ODriveFailure(str(e))
        
    
    def drive_pos(self, left=None, right=None, trajectory=None):
        if not self.driver:
            self.logger.error("Not connected.")
            return
        elif not self.engaged():
            self.logger.error("Not engaged")
            return
        try:
            mode = CTRL_MODE_POSITION_CONTROL if trajectory is None else CTRL_MODE_TRAJECTORY_CONTROL
            if left is not None:
                self.left_axis.controller.config.control_mode = mode
                if trajectory:
                    self.set_trajectory(self.left_axis.trap_traj.config, trajectory)
              #      self.left_axis.controller.move_incremental(left, False)
              #  else:
                self.left_axis.controller.pos_setpoint += left

            
            if right is not None:
                self.right_axis.controller.config.control_mode = mode
                if trajectory:
                    self.set_trajectory(self.right_axis.trap_traj.config, trajectory)
                    self.right_axis.controller.move_incremental(right, False)
                else:
                    self.right_axis.controller.pos_setpoint += right
        except (fibre.protocol.ChannelBrokenException, AttributeError) as e:
           raise ODriveFailure(str(e))

    def set_trajectory(self, traj_config, traj_values):
        """
        Trajectory control value have units related to counts
        """

        assert len(traj_values) == 4, "Trajectory values not 4 elements long"
        traj_config.vel_limit = traj_values[0]
        traj_config.accel_limit = traj_values[1]
        traj_config.decel_limit = traj_values[2]
        traj_config.A_per_css = traj_values[3]

    def drive_current(self, left=None, right=None):
        if not self.driver:
            self.logger.error("Not connected.")
            return
        elif not self.engaged():
            self.logger.error("Not engaged")
            return
        try:
            if left is not None:
                self.left_axis.controller.config.control_mode = CTRL_MODE_CURRENT_CONTROL
                self.left_axis.controller.current_setpoint += left
            if right is not None:
                self.right_axis.controller.config.control_mode = CTRL_MODE_CURRENT_CONTROL
                self.right_axis.controller.current_setpoint += right
        except (fibre.protocol.ChannelBrokenException, AttributeError) as e:
           raise ODriveFailure(str(e))

    def get_errors(self, clear=True):
        # TODO: add error parsing, see: https://github.com/madcowswe/ODrive/blob/master/tools/odrive/utils.py#L34
        if not self.driver:
            return None

        axis_error = self.axes[0].error or self.axes[1].error
        
        if clear:
            for axis in self.axes:
                axis.error = 0
                axis.motor.error = 0
                axis.encoder.error = 0
                axis.controller.error = 0
        
        if axis_error:
            return "error"
        
