"""Module that provides mapping for status property"""

from dataclasses import dataclass


@dataclass
class XiaomiVacuumStatusMapping:
    """Dataclass containing mapping for status property"""

    # vacuum service id
    siid: int = 2

    # status property id in vacuum service
    piid: int = 1

    # idle_at is status property values from https://home.miot-spec.com/spec/model
    # 0,1,2,4,8,10 are common idle states for most xiaomi/xiaomi miot robot-vacuums
    idle_at: tuple[int, ...] = (0, 1, 2, 4, 8, 10)


_NON_STANDARD_STATUS_PROP = [
    (["xiaomi.vacuum.e101gb"], XiaomiVacuumStatusMapping(idle_at=(1, 2, 5, 6, 9, 11, 15, 18, 23))),
    # H50 keeps the firmware version at piid 1 and the status at piid 2.
    # Idle values are the states in which the vacuum stays put: 1 Idle, 2 Charging,
    # 3 BreakCharging, 5 Paused, 9 Charged, 11 Updating, 12 MultiTaskStationWorking,
    # 14 StationWorking, 15 Error, 18 MappingPause, 22 StationAssistingCleaning,
    # 23 StationAssistingCleaned.
    (
        ["xiaomi.vacuum.ov43gb"],
        XiaomiVacuumStatusMapping(piid=2, idle_at=(1, 2, 3, 5, 9, 11, 12, 14, 15, 18, 22, 23)),
    ),
]


def get_status_mapping(model: str) -> XiaomiVacuumStatusMapping:
    return next(
        (mapping for models, mapping in _NON_STANDARD_STATUS_PROP if model in models), XiaomiVacuumStatusMapping()
    )
