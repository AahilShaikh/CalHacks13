import cv2
import numpy as np
import pytest

from aida.scan.camera import CameraCalibration
from aida.scan.foreground import object_foreground
from aida.scan.scaling import ScalingError


def reference():
    K = np.array([[800.,0,400],[0,800,300],[0,0,1]])
    calibration = CameraCalibration(K, np.zeros(5), (800,600),0,.1)
    empty = np.full((600,800,3), 220, np.uint8)
    for y in range(160,450,20):
        for x in range(260,550,20):
            if ((x-400)**2+(y-300)**2)**.5 < 145:
                empty[y:y+10,x:x+10] = 30
    pose = {'R':np.diag([1.,-1,-1]).tolist(), 't_mm':[-100,-100,500]}
    return empty, pose, calibration


def test_empty_table_does_not_become_object():
    empty, pose, calibration = reference()
    with pytest.raises(ScalingError, match='No object'):
        object_foreground(empty, pose, empty, pose, calibration)


def test_removes_printed_board_and_preserves_object():
    empty, pose, calibration = reference()
    frame = empty.copy()
    frame[200:340,360:440] = [20,60,180]
    mask = object_foreground(frame, pose, empty, pose, calibration)
    assert np.mean(mask[205:335,365:435] > 0) > .99
    assert np.count_nonzero(mask[:190]) == 0
    assert np.count_nonzero(mask) == pytest.approx(140*80, rel=.02)


def test_compensates_rotating_board_pattern():
    empty, pose, calibration = reference()
    R0 = np.asarray(pose['R'])
    angle = np.pi/2
    turn = np.array([[np.cos(angle),-np.sin(angle),0],
                     [np.sin(angle),np.cos(angle),0],[0,0,1]])
    R = R0 @ turn
    center = np.array([100,-100,0])
    t = np.asarray(pose['t_mm']) + (R0-R) @ center
    current_pose = {'R':R.tolist(), 't_mm':t.tolist()}
    def h(r,t):
        return calibration.K @ np.column_stack((r[:,:2],t))
    H = h(R,t) @ np.linalg.inv(h(R0,np.asarray(pose['t_mm'])))
    frame = cv2.warpPerspective(empty,H,(800,600),borderValue=(220,220,220))
    frame[200:340,360:440] = [20,60,180]
    mask = object_foreground(frame,current_pose,empty,pose,calibration)
    assert np.count_nonzero(mask) == pytest.approx(140*80,rel=.03)
