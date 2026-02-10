"""Module that provides mapping for map property"""

from dataclasses import dataclass


@dataclass
class XiaomiVacuumPropertyMapping:
    """Dataclass containing mapping for map property"""

    # vacuum map service id
    siid: int = 10

    # current map property id in vacuum map service
    piid: int = 1


_NON_STANDARD_MAP_PROP = [
    (["xiaomi.vacuum.b108gl"], XiaomiVacuumPropertyMapping(siid=7)),
    (
        [
            "xiaomi.vacuum.b108gp",
            "xiaomi.vacuum.ov32gl",
            "xiaomi.vacuum.ov43gl",
            "xiaomi.vacuum.ov51",
            "xiaomi.vacuum.ov81",
        ],
        XiaomiVacuumPropertyMapping(siid=9),
    ),
    (
        [
            "xiaomi.vacuum.b106bk",
            "xiaomi.vacuum.b106eu",
            "xiaomi.vacuum.b106tr",
            "xiaomi.vacuum.b112",
            "xiaomi.vacuum.b112bk",
            "xiaomi.vacuum.b112gl",
            "xiaomi.vacuum.b112tr",
            "xiaomi.vacuum.c101",
            "xiaomi.vacuum.c101eu",
            "xiaomi.vacuum.c102",
            "xiaomi.vacuum.c103",
            "xiaomi.vacuum.c104",
            "xiaomi.vacuum.d106gl",
            "xiaomi.vacuum.e101gl",
        ],
        XiaomiVacuumPropertyMapping(piid=2),
    ),
]


def get_vacuum_map_property(model: str) -> XiaomiVacuumPropertyMapping:
    return next(
        (mapping for models, mapping in _NON_STANDARD_MAP_PROP if model in models), XiaomiVacuumPropertyMapping()
    )
