"""
extraction.py
MCAP file parsing and event data extraction.
Uses mcap.reader.make_reader for all MCAP I/O — no raw byte scanning.
"""

import bisect
from collections import Counter
from datetime import datetime, timezone, timedelta
from mcap.reader import make_reader

# ─────────────────────────────────────────────
# Protobuf
# ─────────────────────────────────────────────
try:
    from google.protobuf import descriptor_pb2, descriptor_pool
    from google.protobuf.message_factory import GetMessageClass
    PROTOBUF_AVAILABLE = True
except ImportError:
    PROTOBUF_AVAILABLE = False

# ─────────────────────────────────────────────
# Topic constants
# ─────────────────────────────────────────────
TOPIC_ACCEL         = "EgoMotionSignalProvision__egoMotionSignal"
TOPIC_AD_STATE      = "StateAutonomousDriving__stateAutonomousDriving"
TOPIC_ODD           = "StateAutonomousDriving__adSmTimeAndReasonToMrm"
TOPIC_MRM           = "RequestL3Maneuver__triggerMrm"
TOPIC_SCG           = "MotionPlanningSafetyConstraints__safetyConstraints"
TOPIC_HMI           = "HMIControlAD__stateAD"
TOPIC_BRAKE_TORQUE  = "TargetBrakingTorqueDriverProvision__targetBrakingTorqueDriverProvision"

AD_STATE_ACTIVE             = 48
OFF_STATE_BRAKING_THRESHOLD = -2.0   # m/s²
OFF_STATE_WINDOW_SECONDS    = 3
POST_OFF_BRAKING_WINDOW     = int(1e9)  # 1s — braking just after OFF still counts for this event
TOR_STATES              = [4, 8, 9, 10]   # CONFIRMATION_REQUEST, BLUE, YELLOW, RED
TOR_ESCALATION_PRIORITY = [10, 9, 8, 4]  # Red > Yellow > Blue > Confirmation (fallback priority)
HMI_STATE_EVENT      = 11   # ACTIVE_HAF_TAF_TOR
HMI_STATE_POST_EVENT = 1    # OFF
REQUIRED_TOPICS = [TOPIC_ACCEL, TOPIC_AD_STATE]

# READY, PREACTIVE, ACTIVATION_PHASE1, ACTIVATION_PHASE2 — startup ramp
# states that are always extended (hidden by default) in the Event
# Timeline's core/extended view, never shown in the core/PDF table even
# as the last row of their HMI group.
STARTUP_STATES = {2, 12, 13, 14}

# ─────────────────────────────────────────────
# State name mappings
# ─────────────────────────────────────────────
ODD_NAMES = {
    0: "Unfilled",
    1: "RoadHazard",
    2: "Congestion",
    3: "RoadClosure",
    4: "AnimalsOnRoad",
    5: "VruLikely",
    6: "HighwayRamp",
    7: "OffHighway",
    8: "CurrentLaneEnds",
    9: "HighwayExitYellowZone",
    10: "HighwayExitRedZone",
    11: "ConstructionSiteYellowZone",
    12: "ConstructionSiteRedZone",
    13: "SevereWeather",
    14: "TollStation",
    15: "PoliceCheckpoint",
    16: "EmergencyVehicleBehind",
    17: "EmergencyVehicleInFront",
    18: "GhostDriver",
    19: "HighwayStart",
    20: "TunnelStart",
    21: "TunnelEnd",
    22: "OncomingTraffic",
    23: "CrossingTraffic",
    24: "RasbDisabledOutsideHighway",
    25: "StandstillAfterEmergencyManeuver",
    26: "Cp60InvalidLaneChange",
    27: "TrafficDensityLow",
    28: "Cp60NotAvailableInCountry",
    29: "Accident",
    30: "ShoulderLane",
    31: "RoadClearanceQualifierNotOk",
    32: "VruAllowedOnLink",
    33: "ManualRoadClearanceClosure",
    34: "DgpsLongitudinalProtectionLevel",
    35: "DgpsProtectionLevelNotOk",
    36: "StrongBrakingTorSuppression",
    37: "OddEventsOverflow",
    38: "LeadingVehicleLostEndOfTrafficJam",
    39: "LeadingVehicleLowReliability",
    40: "ReducedLocalizationPrecision",
    41: "NoPositioningAvailable",
    42: "MostProbablePathEnd",
    43: "IfError",
    44: "Roundabout",
    45: "Ferry",
    46: "Bridge",
    47: "PluralJunction",
    48: "NonL3DrivableMapData",
    49: "Urban",
    50: "MicDegradation",
    51: "LaneRestrictionEvent",
    52: "DisabledVehicleEvent",
    53: "PlannedEvent",
    54: "OtherNewsEvent",
    55: "MiscellaneousEvent",
    56: "RoadDividerAndMedianMissing",
    57: "DriveLeftForEmergencyCorridor",
    58: "DriveRightForEmergencyCorridor",
    59: "OneLaneRoad",
    60: "ExitOnNeighbourLaneAndLowCurvature",
    61: "ExitOnNeighbourLaneAndMediumCurvature",
    62: "ExitOnNeighbourLaneAndHighCurvature",
    63: "NoPositioningAvailablePermanently",
    64: "LeftLaneLeavesHighway",
    65: "RightLaneLeavesHighway",
    66: "VruOnEgoLaneAndRelevantForBraking",
    67: "VruOnEgoOrNextLane",
    68: "VruOnNextNeighboringLane",
    69: "LeadingVehicleMediumReliability",
    70: "CurvatureTooHigh",
    71: "VruDetected",
    72: "LeadingVehicleLost",
    73: "NotWithinLaneBounds",
    74: "NoSeparationFromOncomingTraffic",
    75: "SevereSensorBlockage",
    76: "SevereVisibilityReduction",
    77: "VisibilityReductionDueToSunglare",
    78: "DriverCamBlockedBySteeringwheel",
    79: "DriverCamBlockage",
    80: "ExteriorTempOutOfRange",
    81: "FrontCamReducedVisibility",
    82: "CrashDetected",
    83: "InvalidInput",
    84: "LeadingVehicleTooClose",
    85: "NarrowLane",
    86: "TooManyLvIdChanges",
    87: "SensorBasedEmergencyCorridor",
    88: "FastOvertaker",
    89: "RemainingRangeError",
    90: "DriverNotInSeat",
    91: "SeatOccupancyNotOk",
    92: "SeatbeltUnfastenedDriver",
    93: "SeatbeltUnfastenedPassenger",
    94: "SeatbeltUnfastenedRearLeft",
    95: "SeatbeltUnfastenedRearRight",
    96: "SeatbeltUnfastenedRearMiddle",
    97: "SeatbeltFailure",
    98: "DoorsNotOkay",
    99: "DoorOpenFrontDriverSide",
    100: "DoorOpenFrontPassengerSide",
    101: "DoorOpenRearDriverSide",
    102: "DoorOpenRearPassengerSide",
    103: "MrmInCurrentCycle",
    104: "SpeedAdaptionToNeighboringTraffic",
    105: "EcuHeartbeatLost",
    106: "AbnormalLoad",
    107: "BrakesFailure",
    108: "RemainingRangeLow",
    109: "RemainingRangeCritical",
    110: "TrajectoryUnavailable",
    111: "WashingLiquidLevelLow",
    112: "VehicleGuidingBadHealth",
    113: "OdometryFailure",
    114: "LightSteeringWheelFailure",
    115: "DrivingLightBadHealthStatus",
    116: "HighBeamAssistOff",
    117: "HighBeamAssistBadHealthStatus",
    118: "HmiFailure",
    119: "TrailerRecognition",
    120: "UnstableDrivingSituation",
    121: "AirConditioningOff",
    122: "AirConditioningFailure",
    123: "LateralAccelerationTooHigh",
    124: "TrajectoryTrackingErrorHigh",
    125: "TfcControlErrorElevated",
    126: "HighAvailabilityInactive",
    127: "HighAvailabilityError",
    128: "RoadAssessmentLevelNone",
    129: "RoadAssessmentLevelLow",
    130: "RoadAssessmentLevelMid",
    131: "LongitudinalAccelerationTooHigh",
    132: "LaneGuardLaneChange",
    133: "LaneGuardDegraded",
    134: "HoodOpen",
    135: "HoodNotOk",
    136: "TrunkOpen",
    137: "TrunkNotOk",
    138: "TimeSyncLostIntegrity",
    139: "WiperNotOkay",
    140: "WiperNotInRightMode",
    141: "TurningSignalNotOk",
    142: "TurningSignalActiveByDriver",
    143: "TurningSignalActiveBySystem",
    144: "SaveEnergySupplyNotLocked",
    145: "SaveEnergySupplyNotReadyForHaf",
    146: "SaveEnergySupplyFaulty",
    147: "DynamometerOrEndOfLineRecogNotOkay",
    148: "VehicleOnDynamometerOrAtEndOfLine",
    149: "EcallNotOk",
    150: "TakeOverDetectorFailure",
    151: "GearboxNotInRightStage",
    152: "GearboxNotOk",
    153: "ClimateControlNotOkay",
    154: "ClimateControlSystemNotActive",
    155: "FogOnWindscreen",
    156: "NonValidLaneBoundaryType",
    157: "NonValidLaneWidthAsilA",
    158: "NonValidDashLength",
    159: "PrivacySettingsWrong",
    160: "DrivingDynamicsModeWrong",
    161: "DrivingDynamicsModeFailure",
    162: "FailopSecondaryChannelInit",
    163: "FailopSecondaryChannelReady",
    164: "FailopSecondaryChannelActive",
    165: "FailopPrimaryChannelInit",
    166: "FailopPrimaryChannelReady",
    167: "FailopPrimaryChannelFailure",
    168: "ChannelChangeSohFailure",
    169: "AwaNotOk",
    170: "LateralAccelerationHighThreshold",
    171: "LateralAccelerationLowThreshold",
    172: "WarningLightsActivatedByDriver",
    173: "TrafficLight",
    174: "RoadDividerMissing",
    175: "ControlledAccessMissing",
    176: "MapReducedQualifier",
    177: "MapMissingData",
    178: "InvalidPoseEstimate",
    179: "LaneTypeUnsuitableForCp60",
    180: "LeftSideGoreArea",
    181: "EeFailureHeartbeatPp",
    182: "EeFailureCriticalErrorPp",
    183: "EeFailureHardwareStartupCriticalErrorPp",
    184: "EeFailureSafecodingError",
    185: "EeFailurePadError",
    186: "EeFailureBadQuality",
    187: "EeFailureBadCommunication",
    188: "EeFailureOddEvents",
    189: "FasCp60CodingSwitchNotOk",
    190: "LidarFrontCenterBlockedCleanable",
    191: "LidarFrontCenterBlockedNotCleanable",
    192: "FrrFrontCenterBlockedCleanable",
    193: "FrrFrontCenterBlockedNotCleanable",
    194: "SrrFrontLeftBlockedCleanable",
    195: "SrrFrontLeftBlockedNotCleanable",
    196: "SrrFrontRightBlockedCleanable",
    197: "SrrFrontRightBlockedNotCleanable",
    198: "SrrRearLeftBlockedCleanable",
    199: "SrrRearLeftBlockedNotCleanable",
    200: "SrrRearRightBlockedCleanable",
    201: "SrrRearRightBlockedNotCleanable",
    202: "SrrMidLeftBlockedCleanable",
    203: "SrrMidLeftBlockedNotCleanable",
    204: "SrrMidRightBlockedCleanable",
    205: "SrrMidRightBlockedNotCleanable",
    206: "AdCamMidFrontCenterBlockedCleanable",
    207: "AdCamMidFrontCenterBlockedNotCleanable",
    208: "UcapFrontCenterBlockedCleanable",
    209: "UcapFrontCenterBlockedNotCleanable",
    210: "UcapRearCenterBlockedCleanable",
    211: "UcapRearCenterBlockedNotCleanable",
    212: "BlockageDetectionNotOk",
    213: "EmergencyCorridorAssistantActive",
    214: "Unfilled",
    215: "ConstructionSiteRttiEvent",
    216: "NotWithinLaneBoundsStrict",
    217: "AebFunctionsNotActive",
    218: "AebFunctionsNotOkay",
    219: "AebOverride",
    220: "DriverTookOver",
    221: "DriverTookOverPartially",
    222: "DriverActivated",
    223: "DriverWearingSunglasses",
    224: "DriverOutOfPositionWarning",
    225: "DriverOutOfPositionCritical",
    226: "DriverDrowsy",
    227: "DriverIncapableForTakeOver",
    228: "DriverFatigueWarning",
    229: "DriverFatigueCritical",
    230: "SpeedExceedsReadyUpperThreshold",
    231: "SpeedExceedsReadyLowerThreshold",
    232: "VehicleDrivesBackward",
    233: "DrivingDirectionSignalNotOk",
    234: "MaxOperatingSpeed",
    235: "AsildSpeedNotOk",
    236: "XccFunctionsActive",
    237: "XccFunctionsTriggerTor",
    238: "XccFunctionsNotOkay",
    239: "LateralFunctionsActive",
    240: "LateralFunctionsTriggerTorOrHor",
    241: "AlgSwitchedOn",
    242: "FusedLightSignalsFailure",
    243: "ParkingFunctionsActive",
    244: "ParkingFunctionsFailure",
    245: "ObjectInCloseRange",
    246: "NoSfaSubscription",
    247: "SubFeaturesNotActive",
    248: "FlashingTrafficLight",
    249: "StopTrafficLight",
    250: "ParkingBrakeFailure",
    251: "DriverActivatedParkingBrake",
    252: "InvalidNewTrajectoryAtTfc",
    253: "HazardBrakeLightFailure",
    254: "RemoteActivatabilityNotGranted",
    255: "DriverActivatedMainBeamLight",
    256: "HighAvailabilityQualifierNotAsil",
    257: "EdrFailure",
    258: "EdrNotActive",
    259: "GeofenceUnterschleissheim",
    260: "GeofenceDingolfing",
    261: "GeofenceSokolov",
    262: "GeofenceMiramas",
    263: "DiagRequestSpeedReduction",
    264: "VelocityTooHighAndNoLv",
    265: "MpadHighLatentFailure",
    266: "MpadAddonLatentFailure",
    267: "VehicleServiceRequested",
    268: "Unfilled",
    269: "RemoteActivatabilityConfigurationMismatch",
    270: "LaneInhibitionAnnouncement",
    271: "StopTrafficLightNotBrakingRelevant",
    272: "ChannelSwitchToFallbackMrmTriggeredByAdcoev",
    273: "AdCamMidFrontCenterBlockedNotCalibrated",
    274: "LidarFrontCenterBlockedNotCalibrated",
    275: "FrrFrontCenterBlockedNotCalibrated",
    276: "SensorBlockedNotCalibrated",
    277: "GeofenceAschheim",
    278: "GeofenceMaisach",
    279: "Unfilled",
    280: "TfcMaxBrakeSet",
    281: "Unfilled",
    282: "Cp60Initialization",
    283: "HighCurvatureWithNotBestMapCandidate",
    284: "RoadCurvatureAboveThreshold",
    285: "LongitudinalAccelerationExtremelyHigh",
    286: "VehicleGuidingTyrePressureMinorFailure",
    287: "Unfilled",
    288: "SfaInvalid",
    289: "Unfilled",
    290: "PreventReadyAfterDeactivation",
    291: "Unfilled",
    292: "Unfilled",
    293: "LeadingVehicleNotClassifiedLeadingByAllSensors",
    294: "Unfilled",
    295: "Unfilled",
    296: "TimeSyncLostIntegrityOdo",
    297: "TimeSyncLostIntegrityLidar",
    298: "TimeSyncLostIntegrityAdcam",
    299: "TimeSyncLostIntegrityFrr",
    300: "LcaSwitchedOn",
    301: "LcaActiveIntervention",
    302: "PfgsawReportsFailure",
    303: "PfgsawActiveIntervention",
    304: "PfgsawNotActive",
    305: "AirbagOrFasEdrOrDssadFailure",
    306: "TrailerHitchActive",
    307: "TrailerRecognitionFailure",
    308: "NhaActive",
    309: "AdmissionGuidingFunctionFailure",
    310: "DacBreakRecommendation",
    311: "GwwIntervention",
    312: "Unfilled",
    313: "SwaSwitchedOn",
    314: "WwaIntervention",
    315: "FogLightsOn",
    316: "FogLightsNotOk",
    317: "BrakingLightFailure",
    318: "HeadLightBeamThrowAdjustFailure",
    319: "TrajectoryVelocityLowThreshold",
    320: "ValetModeActive",
    321: "DecelerationExceedsThreshold",
    322: "TrajectoryCurvatureAboveThreshold",
    323: "AdStateMachinePassivated",
    324: "FallbackSmControlInputsFailure",
    325: "RoadAssessmentFailure",
    326: "RoadAssessmentFailureBadWeather",
    327: "RoadAssessmentFailureUnsuitableLane",
    328: "VehiclePermanentStandstill",
    329: "VehicleGuidingCriticalFailure",
    330: "VehicleGuidingComFailure",
    331: "VehicleGuidingWarning",
    332: "VehicleGuidingMinorFailure",
    333: "VehicleGuidingMajorFailure",
    334: "FeedbackTrackingControllerRfeFailure",
    335: "PostCrashIbrakeFailure",
    336: "TrajectoryPropertiesInvalid",
    337: "CrashDetectionFailure",
    338: "MpadAddonPpMajorFailure",
    339: "PreventActivationAfterUnstableDriving",
    340: "UnstableDrivingDetectionFailure",
    341: "FailopSecondaryChannelFailure",
    342: "DrivingLightSwitchedOffAndAutoModeNotActive",
    343: "MpadHighPpMajorFailure",
    344: "TakeOverDetectorHodAndButtonFailure",
    345: "RecwFailure",
    346: "RecwActiveWarning",
    347: "TakeOverDetectorHodFailure",
    348: "CustomerDidNotAcceptServiceAgreement",
    349: "MpadAddonPpCriticalFailure",
    350: "DriverEyesNotVisibleWarning",
    351: "DriverEyesNotVisibleCritical",
    352: "DriverActivatedWithSpeech",
    353: "DriverDeactivatedWithSpeech",
    354: "DriverTriesTakeoverByStrongBraking",
    355: "DriverTriesTakeoverByStrongSteering",
    356: "DriverTookOverPartiallyWithBrakePedal",
    357: "DriverTookOverPartiallyWithAcceleratorPedal",
    358: "DriverTookOverPartiallyWithSteeringTorque",
    359: "DriverTookOverPartiallyWithL3Button",
    360: "DriverTookOverPartiallyWithHandsOn",
    361: "DriverKickDownAcceleratorPedal",
    362: "DriverPressesAcceleratorPedalButNoTakeOver",
    363: "DriverPressesBrakePedal",
    364: "DriverCamActivation",
    365: "DriverFatigueInvalid",
    366: "DriverAppliesStrongSteeringTorque",
    367: "DriverHasTwoHandsOnSteeringWheel",
    368: "DriverHasOneHandOnSteeringWheel",
    369: "HornActivatedByDriver",
    370: "DriverAppliesSteeringTorque",
    371: "DriverXxx",
    372: "DriverXxx",
    373: "DriverXxx",
    374: "DriverXxx",
    375: "DriverXxx",
    376: "DriverXxx",
    377: "DriverXxx",
    378: "DriverXxx",
    379: "DriverXxx",
    380: "DriverXxx",
    381: "DriverXxx",
    382: "DriverXxx",
    383: "DriverXxx",
    384: "DriverXxx",
    385: "DriverXxx",
    386: "DriverXxx",
    387: "DriverXxx",
    388: "DriverXxx",
    389: "DriverXxx",
    390: "EmergencyVehicleNotCritical",
    391: "EmergencyVehicleDetected",
    392: "EmergencyVehicleDetectionFailure",
    393: "TemperatureFailure",
    394: "LeadingVehicleReliabilityFailureNotDebounced",
    395: "EmergencyCorridorNotSuitable",
    396: "ConstructionSiteDetectionLostIntegrity",
    397: "LeadingVehicleWrongType",
    398: "TouristModeLeftHandTraffic",
    399: "TouristModeFailure",
    400: "SlipperyRoad",
    401: "MediumWiperSpeed",
    402: "HighWiperSpeed",
    403: "WiperSpeedDetectionError",
    404: "BadWeatherDetectionSensor",
    405: "FusedObjectsQualifierNotAsil",
    406: "BadWeather",
    407: "ExteriorExtremelyTempOutOfRange",
    408: "SafetyAssessmentStatusInvalid",
    409: "Unfilled",
    410: "RemoteActivatabilitySwUpdateRequired",
    411: "RemoteActivatabilityError",
    412: "RemoteActivatabilityNotAvailableInCountry",
    413: "RemoteActivatabilityNotAvailableInRegion",
    414: "RemoteActivatabilityVehicleNotMapped",
    415: "RemoteActivatabilityMismatch",
    416: "CscmFailure",
    417: "RemoteActivatabilityNoResponseFromBackend",
    418: "RemoteActivatabilityInvalidSignal",
    419: "LidarInitializing",
    420: "OdometryCriticalFailure",
    421: "OdometryWarning",
    422: "OdometryComFailure",
    423: "OdometryMajorFailure",
    424: "MpadHighPpCriticalFailure",
    425: "VelocityAboveHysteresisThreshold",
    426: "CustomerProfileNotReady",
    427: "RoadAssessmentBestCandidateNotMap",
    428: "SrrMidRightBlockedPermanentFailure",
    429: "SrrMidLeftBlockedPermanentFailure",
    430: "SrrRearRightBlockedPermanentFailure",
    431: "SrrRearLeftBlockedPermanentFailure",
    432: "SrrFrontRightBlockedPermanentFailure",
    433: "SrrFrontLeftBlockedPermanentFailure",
    434: "SrrMidRightBlockedEnvironmental",
    435: "SrrMidLeftBlockedEnvironmental",
    436: "SrrRearRightBlockedEnvironmental",
    437: "SrrRearLeftBlockedEnvironmental",
    438: "SrrFrontRightBlockedEnvironmental",
    439: "SrrFrontLeftBlockedEnvironmental",
    440: "SrrMidRightBlockedTemporaryFailure",
    441: "SrrMidLeftBlockedTemporaryFailure",
    442: "SrrRearRightBlockedTemporaryFailure",
    443: "SrrRearLeftBlockedTemporaryFailure",
    444: "SrrFrontRightBlockedTemporaryFailure",
    445: "SrrFrontLeftBlockedTemporaryFailure",
    446: "AdCamMidFrontCenterBlockedEnvironmental",
    447: "AdCamMidFrontCenterBlockedTemporaryFailure",
    448: "AdCamMidFrontCenterBlockedPermanentFailure",
    449: "FrrFrontCenterBlockedEnvironmental",
    450: "FrrFrontCenterBlockedTemporaryFailure",
    451: "FrrFrontCenterBlockedPermanentFailure",
    452: "LidarFrontCenterBlockedEnvironmental",
    453: "LidarFrontCenterBlockedTemporaryFailure",
    454: "LidarFrontCenterBlockedPermanentFailure",
    455: "UcapRearCenterBlockedPermanentFailure",
    456: "UcapFrontCenterBlockedPermanentFailure",
    457: "UcapLeftOrRightBlockedPermanentFailure",
    458: "UcapRearCenterBlockedEnvironmental",
    459: "UcapFrontCenterBlockedEnvironmental",
    460: "UcapRearCenterBlockedTemporaryFailure",
    461: "UcapFrontCenterBlockedTemporaryFailure",
    462: "UcapLeftOrRightBlockedTemporaryFailure",
    463: "UcapLeftOrRightBlockedNotCleanable",
    464: "UcapLeftOrRightBlockedEnvironmental",
    465: "UltrasoundFrontCenterBlockedNotCleanable",
    466: "UltrasoundFrontCenterBlockedEnvironmental",
    467: "UltrasoundFrontCenterBlockedTemporaryFailure",
    468: "UltrasoundFrontCenterBlockedPermanentFailure",
    469: "AdSmAddonIsInStateOff",
    470: "FrontWindowCleaningActive",
    471: "SafetyAssessmentAccelerationOverrideNotOk",
    472: "CriticalSensorBlockage",
    473: "MpScgAccelerationTooHigh",
    474: "MpScgSideCollisionAvoidanceActive",
    475: "ParamServerQualifierNotOk",
    476: "PpHighCycleTimeTooHigh",
    477: "PpAddonCycleTimeTooHigh",
    478: "TfcControlErrorCritical",
    479: "LastElement",
    65535: "Invalid",
}

MRM_ODD_CODES = {
    26, 110, 120, 125, 128, 129, 130, 132, 143, 144, 145,
    146, 172, 219, 252, 311, 325, 164, 103, 123,
}

DRIVER_TAKEOVER_ODD_CODES = {66, 67, 68, 71, 354, 355}

# Strong-braking/strong-steering takeover codes that mark the true start of
# the Event Triggered phase within an HMI=11 window — any ODD sub-segment
# before the first one of these is really still Pre-Event.
PRIO3_ODD_CODES = {354, 355}

# Continuous background monitoring codes that fire every ~40ms throughout a
# recording — should be deprioritized in Pre Event ODD lookup in favour of
# discrete driver-action or event codes when both are present nearby.
BACKGROUND_NOISE_ODD_CODES = {
    319,  # TrajectoryVelocityLowThreshold
    321,  # DecelerationExceedsThreshold
    322,  # TrajectoryCurvatureAboveThreshold
    323,  # AdStateMachinePassivated
    324,  # FallbackSmControlInputsFailure
    325,  # RoadAssessmentFailure
    473,  # MpScgAccelerationTooHigh
    474,  # MpScgSideCollisionAvoidanceActive
}

CRASH_ODD_CODES = {82}

HMI_NAMES = {
    0:  "Temporary_NOT_AVAILABLE",
    1:  "OFF",
    2:  "READY",
    3:  "ACTIVE",
    4:  "ACTIVE_CONFIRMATION_REQUEST",
    5:  "ACTIVE_ATTENTION_REQUEST",
    6:  "ACTIVE_FATIGUE_REQUEST",
    7:  "ACTIVE_UNDO_REQUEST",
    8:  "ACTIVE_TAKE_OVER_REQUEST_BLUE",
    9:  "ACTIVE_TAKE_OVER_REQUEST_YELLOW",
    10: "ACTIVE_TAKE_OVER_REQUEST_RED",
    11: "ACTIVE_HAF_TAF_TOR",
    12: "PREACTIVE",
    13: "ACTIVATION_PHASE1",
    14: "ACTIVATION_PHASE2",
    15: "ACTIVE_MRM_RISK_REDUCTION",
    16: "ACTIVE_MRM_STABILIZATION",
    17: "ACTIVE_MRM_EMERGENCY_STOP",
    18: "ACTIVE_MRM_STANDSTILL",
    19: "PERMANENTLY_NOT_AVAILABLE",
    20: "ACTIVE_EMERGENCY_MANEUVER",
}

HMI_STATE_NAMES = {
    0:  "Temporary_NOT_AVAILABLE",
    1:  "OFF",
    2:  "READY",
    3:  "ACTIVE",
    4:  "ACTIVE_CONFIRMATION_REQUEST",
    5:  "ACTIVE_ATTENTION_REQUEST",
    6:  "ACTIVE_FATIGUE_REQUEST",
    7:  "ACTIVE_UNDO_REQUEST",
    8:  "ACTIVE_TAKE_OVER_REQUEST_BLUE",
    9:  "ACTIVE_TAKE_OVER_REQUEST_YELLOW",
    10: "ACTIVE_TAKE_OVER_REQUEST_RED",
    11: "ACTIVE_HAF_TAF_TOR",
    12: "PREACTIVE",
    13: "ACTIVATION_PHASE1",
    14: "ACTIVATION_PHASE2",
    15: "ACTIVE_MRM_RISK_REDUCTION",
    16: "ACTIVE_MRM_STABILIZATION",
    17: "ACTIVE_MRM_EMERGENCY_STOP",
    18: "ACTIVE_MRM_STANDSTILL",
    19: "PERMANENTLY_NOT_AVAILABLE",
    20: "ACTIVE_EMERGENCY_MANEUVER",
}

HMI_TOR_LABELS = {
    4:  "Confirmation Request",
    8:  "Blue",
    9:  "Yellow",
    10: "Red",
}

# Phase label shown in the Event Timeline table for each key HMI state value.
# Intermediate activation states (READY=2, PREACTIVE=12, etc.) fall through to
# HMI_STATE_NAMES so their full name is used as the phase label.
PHASE_LABELS = {
    3:  "Pre Event",
    4:  "Pre-Event",
    8:  "Pre-Event",
    9:  "Pre-Event",
    10: "Pre-Event",
    11: "Event Triggered",
    1:  "Post-Event",
}

AD_NAMES = {
    1:  "Off",
    2:  "Init",
    4:  "Ready",
    8:  "Preactive",
    48: "Active",
}


# ─────────────────────────────────────────────
# Schema registry
# ─────────────────────────────────────────────
def _build_schema_registry(summary):
    """
    Build {schema_id: protobuf_message_class} from schemas embedded in the MCAP file.
    Dependencies are added to the pool in topological order before creating classes.
    """
    if not PROTOBUF_AVAILABLE or summary is None:
        return {}

    pool     = descriptor_pool.DescriptorPool()
    registry = {}

    # Parse all FileDescriptorProtos from MCAP schemas.
    # Each schema embeds a FileDescriptorSet (not a bare FileDescriptorProto),
    # so iterate fds.file to collect all included proto descriptors.
    file_protos = {}
    for schema in summary.schemas.values():
        if schema.encoding == "protobuf":
            try:
                fds = descriptor_pb2.FileDescriptorSet()
                fds.ParseFromString(schema.data)
                for fdp in fds.file:
                    file_protos[fdp.name] = (schema.id, fdp)
            except Exception:
                pass

    # Add to pool in dependency order
    added = set()

    def _add(name):
        if name in added or name not in file_protos:
            return
        _, fdp = file_protos[name]
        for dep in fdp.dependency:
            _add(dep)
        try:
            pool.Add(fdp)
        except Exception:
            pass
        added.add(name)

    for name in list(file_protos):
        _add(name)

    # Create one message class per schema.
    # Use schema.name (strip .proto suffix) as the fully-qualified message name
    # so the lookup is independent of package prefix or message_type ordering.
    for schema in summary.schemas.values():
        if schema.encoding != "protobuf":
            continue
        full_name = schema.name.removesuffix(".proto")
        try:
            msg_desc  = pool.FindMessageTypeByName(full_name)
            msg_class = GetMessageClass(msg_desc)
            registry[schema.id] = msg_class
        except Exception:
            pass

    return registry


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def _get_phase_label(hmi_val, ts, first_tor_ts):
    """
    Returns the phase label for a given HMI value/timestamp.
    Known key phases use the static PHASE_LABELS mapping. Intermediate
    states not in PHASE_LABELS (e.g. HMI=5,6,7) are boundary-aware instead
    of falling back to their raw enum name: they resolve to "Pre Event" if
    they occur before first_tor_ts (still in ACTIVE, pre-TOR-escalation),
    or "Pre-Event" once at/after first_tor_ts (during TOR escalation).
    """
    if hmi_val in PHASE_LABELS:
        return PHASE_LABELS[hmi_val]
    if first_tor_ts is not None and ts < first_tor_ts:
        return "Pre Event"
    return "Pre-Event"


def _mark_core_extended(rows):
    """
    Mark each Event Timeline row as core (is_extended=False) or extended
    (is_extended=True), for the analysis.html collapsible view and the
    PDF report's minimal table.
    HMI=1 (Post-Event) rows are always core regardless of grouping.
    HMI=11 (Event Triggered) sub-rows are core, except a leading
    "Pre-Event" sub-row (the pre-Prio3 lead-in) whose ODD is a carryover
    of the last ODD from the preceding TOR escalation phase (HMI=4/8/9/10)
    — that sub-row adds no new information and is extended (hidden).
    STARTUP_STATES (READY/PREACTIVE/ACTIVATION_PHASE1/2) are always
    extended, even as the last row of their group — they're startup ramp
    noise, not meaningful phase content. HMI=3 is always extended when
    HMI=4 (ACTIVE_CONFIRMATION_REQUEST) also exists in the timeline —
    HMI=4 captures the actual pre-event context in that pattern, making
    HMI=3 just background. Every other HMI value (TOR escalation states,
    oscillating intermediate rows, plain HMI=3 when no HMI=4 exists,
    etc.): within each consecutive run of rows sharing that HMI value,
    only the LAST row is core — earlier sub-rows are extended.
    """
    has_hmi4 = any(r["hmi_value"] == 4 for r in rows)

    last_pre_event_odd = None
    for r in rows:
        if r.get("hmi_value") in (4, 8, 9, 10):
            last_pre_event_odd = r.get("odd_code")

    result = []
    i = 0
    n = len(rows)
    while i < n:
        hmi = rows[i]["hmi_value"]
        group = []
        while i < n and rows[i]["hmi_value"] == hmi:
            group.append(rows[i])
            i += 1
        if hmi == 11:
            for row in group:
                if row.get("phase") == "Pre-Event":
                    row["is_extended"] = row.get("odd_code") == last_pre_event_odd
                else:
                    row["is_extended"] = False
        elif hmi == 1:
            for row in group:
                row["is_extended"] = False
        elif hmi in STARTUP_STATES:
            for row in group:
                row["is_extended"] = True
        elif hmi == 3 and has_hmi4:
            for row in group:
                row["is_extended"] = True
        else:
            last_idx = len(group) - 1
            for j, row in enumerate(group):
                row["is_extended"] = j < last_idx
        result.extend(group)
    return result


def classify_event_type(odd_code):
    """Return a high-level event category string for the given ODD code."""
    odd = int(odd_code) if odd_code else 0
    if odd in CRASH_ODD_CODES:
        return "Crash Event"
    elif odd in DRIVER_TAKEOVER_ODD_CODES:
        return "Strong Braking"
    else:
        return "Normal Event"


def _get_field(msg, path):
    """Traverse a dot-separated field path on a protobuf message. Returns None on failure."""
    try:
        obj = msg
        for part in path.split("."):
            obj = getattr(obj, part)
        return obj
    except Exception:
        return None


def _to_kmh(v):
    return v  # velocity already stored in km/h in MCAP


def _classify_braking(val):
    """Classify longitudinal deceleration (m/s²) into a severity label."""
    if val is None:
        return "Not detected"
    if val < -8.0:
        return "Emergency Braking"
    if val < -5.0:
        return "Hard Braking"
    if val < -3.0:
        return "Moderate Braking"
    return "Light Braking"


def _normalize_timeseries(pairs, max_points=500):
    """
    Convert [(ns_timestamp, value), ...] to [{'t': seconds, 'v': value}, ...].
    Downsamples to max_points to keep chart rendering responsive.
    """
    if not pairs:
        return []
    t0     = pairs[0][0]
    result = [{"t": round((ts - t0) / 1e9, 3), "v": round(v, 4)} for ts, v in pairs]
    if len(result) > max_points:
        step   = len(result) // max_points
        result = result[::step]
    return result


def _extract_max_braking(accel_pairs, window_start, window_end, first_tor_ts=None):
    """
    Try the standard window [window_start, window_end] first.
    If it yields zero AD=48 accel points and first_tor_ts is available,
    expand the window to start from first_tor_ts to capture braking that
    occurred during the TOR escalation phase before AD disengaged.
    Returns (points, used_fallback_window).
    """
    if window_start is None:
        return accel_pairs, False

    if window_end is not None:
        standard_points = [(t, v) for t, v in accel_pairs
                           if window_start <= t <= window_end]
    else:
        standard_points = [(t, v) for t, v in accel_pairs if t >= window_start]

    if standard_points:
        return standard_points, False

    if first_tor_ts is not None and first_tor_ts < window_start:
        if window_end is not None:
            expanded_points = [(t, v) for t, v in accel_pairs
                               if first_tor_ts <= t <= window_end]
        else:
            expanded_points = [(t, v) for t, v in accel_pairs if t >= first_tor_ts]
        if expanded_points:
            return expanded_points, True

    return [], False


def _detect_off_state_braking_spike(all_accel_pairs, post_event_ts,
                                    window_seconds=3, min_gap=1.5):
    """
    Detect a sudden braking spike during the OFF-state window by comparing the
    lowest value against the baseline average of all other points in the window.
    Only flags genuine isolated drops — not generic negative values or sensor noise.
    min_gap: minimum difference (m/s²) between spike and baseline to qualify.
    """
    if post_event_ts is None:
        return None
    window_end = post_event_ts + int(window_seconds * 1e9)
    off_points = [(t, v) for t, v in all_accel_pairs if post_event_ts <= t <= window_end]
    if len(off_points) < 3:
        return None
    values  = [v for _, v in off_points]
    min_val = min(values)
    min_idx = values.index(min_val)
    baseline_values = [v for i, v in enumerate(values) if i != min_idx]
    if not baseline_values:
        return None
    baseline_avg = sum(baseline_values) / len(baseline_values)
    return min_val if (baseline_avg - min_val) >= min_gap else None


def _confirm_no_driver_brake_input(driver_brake_torque_pairs, post_event_ts,
                                   window_seconds=3):
    """
    Return True if driver brake torque stays at zero throughout the OFF-state
    window (no direct pedal input). Returns None if signal is unavailable.
    """
    if post_event_ts is None or not driver_brake_torque_pairs:
        return None
    window_end = post_event_ts + int(window_seconds * 1e9)
    window_points = [v for t, v in driver_brake_torque_pairs if post_event_ts <= t <= window_end]
    if not window_points:
        return None
    return all(v == 0 for v in window_points)


def _resolve_odd_code(name):
    try:
        return int(name)
    except (ValueError, TypeError):
        return next(
            (k for k, v in ODD_NAMES.items() if v == str(name).strip()), None
        )


def _has_354_355_anywhere(odd_timeline):
    for _t, name in odd_timeline:
        if name and str(name).strip() not in ("", "N/A"):
            if _resolve_odd_code(name) in (354, 355):
                return True
    return False


def _find_odd_before(target_code, odd_timeline):
    """
    Find the ODD code that was active immediately before the given
    target_code first appears in the timeline. Generic — works for any
    target ODD code, not specific to one value.
    """
    if target_code is None:
        return None
    target_times = [
        t for t, name in odd_timeline
        if _resolve_odd_code(name) == target_code
    ]
    if not target_times:
        return None
    first_target_ts = min(target_times)
    before_target = sorted(
        [(t, name) for t, name in odd_timeline if t < first_target_ts],
        key=lambda x: x[0],
        reverse=True,
    )
    for _t, name in before_target:
        code = _resolve_odd_code(name)
        if code != target_code:
            return code
    return None


def _get_analysis_anchor(hmi_timeline, event_triggered_ts):
    """
    Returns (anchor_ts, anchor_hmi_val, anchor_end_ts, is_fallback).

    When HMI=11 (HAF_TAF_TOR) is present, uses it as the anchor unchanged.
    When HMI=11 is absent, falls back to the highest TOR escalation state
    reached (Red=10 > Yellow=9 > Blue=8), using its first occurrence as the
    anchor and the next HMI transition as the window end.
    """
    if event_triggered_ts is not None:
        return event_triggered_ts, 11, None, False

    for tor_level in TOR_ESCALATION_PRIORITY:
        matches = [(t, v) for t, v, *_ in hmi_timeline if v == tor_level]
        if matches:
            anchor_ts = min(matches, key=lambda x: x[0])[0]
            later = sorted(
                [(t, v) for t, v, *_ in hmi_timeline if t > anchor_ts],
                key=lambda x: x[0],
            )
            anchor_end_ts = later[0][0] if later else None
            return anchor_ts, tor_level, anchor_end_ts, True

    return None, None, None, False


def _error_result(msg):
    return {
        "max_braking":          "Error",
        "max_braking_raw":      None,
        "braking_severity":     "Error",
        "velocity_at_event":    "N/A",
        "velocity_at_trigger":  "N/A",
        "min_velocity_after":   "N/A",
        "odd":                  "Error",
        "odd_trigger":          "N/A",
        "odd_event_name":       "N/A",
        "event_odd_code":       None,
        "event_type":           "Normal Event",
        "suggested_classification": "Normal Event",
        "mrm_triggered":        "No",
        "scg":                  "Error",
        "scg_reason_text":      "N/A",
        "scg_reason_value":     0,
        "total_messages":       0,
        "ad_active_messages":   0,
        "accel_data_points":    0,
        "status":               f"Error: {msg}",
        "errors":               [msg],
        "accel_timeseries":     [],
        "velocity_timeseries":  [],
        "event_table":          [],
        "analysis_summary":     {},
        "analysis_anchor_note":          None,
        "braking_window_note":           None,
        "odd_code_not_in_state_machine": False,
        "off_state_braking_raw":         None,
        "no_driver_brake_input":         None,
        "post_event_note":               None,
    }


# ─────────────────────────────────────────────
# Validation
# ─────────────────────────────────────────────
def validate_mcap(filepath):
    """
    Verify the MCAP file is readable and contains the required topics.
    Returns (is_valid: bool, message: str, missing_topics: list).
    """
    try:
        with open(filepath, "rb") as f:
            reader  = make_reader(f)
            summary = reader.get_summary()

        if summary is None:
            return False, "File is empty or unreadable.", list(REQUIRED_TOPICS)

        available = {ch.topic for ch in summary.channels.values()}
        missing   = [r for r in REQUIRED_TOPICS if not any(r in t for t in available)]

        if missing:
            return False, f"Missing required topics: {missing}", missing

        return True, "Valid", []

    except Exception as e:
        err = str(e).lower()
        if "descriptor" in err or "protobuf" in err:
            msg = "Unable to decode this file as a valid MCAP recording. The file may be corrupted or in an unsupported format."
        elif "topic" in err:
            msg = "This MCAP file is missing required data topics. It may be an incomplete recording."
        else:
            msg = f"Could not read the MCAP file: {e}"
        return False, msg, list(REQUIRED_TOPICS)


# ─────────────────────────────────────────────
# Conclusion generator (all ODD types)
# ─────────────────────────────────────────────
def generate_conclusion(event_odd_code, pre_event_hmi_val, odd_code_not_in_state_machine):
    tor_label = HMI_TOR_LABELS.get(pre_event_hmi_val, "an unidentified")

    if odd_code_not_in_state_machine:
        return (
            f"Conclude trigger reason as expected "
            f"behavior due to {tor_label} TOR."
        )

    if event_odd_code == 355:
        takeover_action = "take over the steering"
    elif event_odd_code == 354:
        takeover_action = "take over by strong braking"
    else:
        takeover_action = "take over"

    return (
        f"Considered as expected behavior as the driver "
        f"want to {takeover_action} due to TOR {tor_label}."
    )


# ─────────────────────────────────────────────
# Analysis summary generator
# ─────────────────────────────────────────────
def generate_analysis_summary(extraction_data):
    """
    Generate automated analysis text fields from an extraction result dict.
    Returns a dict of pre-formatted summary strings for use in the report.
    """
    event_table = extraction_data.get("event_table", [])

    # Look up phases by name so the table can have variable length (dynamic HMI sequence)
    # Use the LAST "Pre-Event" row (closest to HMI=11) — multi-step MCAPs have several.
    _pre_event_rows = [r for r in event_table if r.get("phase") == "Pre-Event"]
    _pre_event_row  = _pre_event_rows[-1] if _pre_event_rows else {}
    _event_trigger_row = next((r for r in event_table if r.get("phase") == "Event Triggered"), {})

    pre_event_odd_code = _pre_event_row.get("odd_code")
    pre_event_odd_name = _pre_event_row.get("odd_triggered", "N/A")
    pre_event_duration = _pre_event_row.get("duration_seconds")

    event_odd_code = _event_trigger_row.get("odd_code")
    event_duration = _event_trigger_row.get("duration_seconds")

    max_braking         = extraction_data.get("max_braking", "N/A")
    max_braking_raw     = extraction_data.get("max_braking_raw")
    velocity_at_trigger = extraction_data.get("velocity_at_trigger", "N/A")
    min_velocity_after  = extraction_data.get("min_velocity_after", "N/A")

    # TOR HMI state (last TOR-adjacent state before HMI=11) drives every
    # "which TOR fired" reference below — never hardcode a color/state here.
    pre_event_hmi_val = extraction_data.get("pre_event_hmi_val")
    tor_label = HMI_STATE_NAMES.get(pre_event_hmi_val) if pre_event_hmi_val is not None else None

    # summary_odd_pre_event
    if pre_event_odd_code is not None:
        summary_odd_pre_event = (
            f"ODD{pre_event_odd_code} ({pre_event_odd_name}) triggering "
            f"{tor_label or 'ACTIVE_TAKE_OVER_REQUEST_YELLOW'} for {pre_event_duration}s."
        )
    else:
        summary_odd_pre_event = "No ODD detected in Pre Event phase."

    # summary_cause_takeover
    if event_odd_code in MRM_ODD_CODES:
        summary_cause_takeover = (
            f"This causes MRM to be triggered and HMI state changes to "
            f"ACTIVE_HAF_TAF_TOR for {event_duration}s."
        )
    elif event_odd_code == 355:
        summary_cause_takeover = (
            f"This causes the driver to take over by strong steering and "
            f"HMI state changes to ACTIVE_HAF_TAF_TOR for {event_duration}s."
        )
    elif event_odd_code == 354:
        summary_cause_takeover = (
            f"This causes the driver to take over by strong braking and "
            f"HMI state changes to ACTIVE_HAF_TAF_TOR for {event_duration}s."
        )
    else:
        summary_cause_takeover = (
            f"HMI state changes to ACTIVE_HAF_TAF_TOR for {event_duration}s."
        )

    # summary_driver_input — only for ODD 354/355
    if event_odd_code == 355:
        summary_driver_input = (
            "There is also steering input by the driver showing that the "
            "driver take over the steering."
        )
    elif event_odd_code == 354:
        summary_driver_input = (
            "There is also braking input by the driver showing that the "
            "driver take over the braking."
        )
    else:
        summary_driver_input = ""

    # summary_max_braking — velocity values already include the km/h unit string
    if velocity_at_trigger == "Vehicle Standstill":
        summary_max_braking = (
            f"During on state the maximum braking is {max_braking} during AD state Active. "
            f"The vehicle was at standstill during the event."
        )
    else:
        summary_max_braking = (
            f"During on state the maximum braking is {max_braking} during AD state Active "
            f"and vehicle speed decrease from {velocity_at_trigger} to "
            f"{min_velocity_after}."
        )

    # summary_off_state_braking — only when noticeable braking occurs after HMI=1
    # and driver brake torque confirms it is NOT from direct pedal input
    off_state_braking_raw = extraction_data.get("off_state_braking_raw")
    no_driver_brake_input = extraction_data.get("no_driver_brake_input")
    if off_state_braking_raw is not None and no_driver_brake_input:
        summary_off_state_braking = (
            f"During OFF state the maximum braking is "
            f"{off_state_braking_raw:.4f} m/s² with no driver "
            f"brake pedal input detected."
        )
    else:
        summary_off_state_braking = ""

    # summary_driver_takeover — only for ODD 354/355, conditioned on detected input
    v_trigger_kmh = extraction_data.get("velocity_at_trigger_kmh")
    v_min_kmh     = extraction_data.get("min_velocity_after_kmh")
    if event_odd_code == 354:
        if max_braking_raw is not None and max_braking_raw < -1.0 and tor_label is not None:
            summary_driver_takeover = f"The driver take over because {tor_label} is triggered."
        else:
            summary_driver_takeover = ""
    elif event_odd_code == 355:
        vel_change = abs((v_trigger_kmh or 0) - (v_min_kmh or 0))
        if vel_change > 2 and tor_label is not None:
            summary_driver_takeover = f"The driver take over because {tor_label} is triggered."
        else:
            summary_driver_takeover = ""
    else:
        summary_driver_takeover = ""

    # summary_false_positive
    if max_braking_raw is not None and max_braking_raw < -4.0:
        summary_false_positive = "False Positive"
    else:
        summary_false_positive = "True Positive"

    # summary_conclusion — generated for ALL ODD types
    odd_code_not_in_state_machine = extraction_data.get("odd_code_not_in_state_machine", False)
    summary_conclusion = generate_conclusion(
        event_odd_code,
        extraction_data.get("pre_event_hmi_val"),
        odd_code_not_in_state_machine,
    )

    return {
        "summary_odd_pre_event":      summary_odd_pre_event,
        "summary_cause_takeover":     summary_cause_takeover,
        "summary_driver_input":       summary_driver_input,
        "summary_max_braking":        summary_max_braking,
        "summary_off_state_braking":  summary_off_state_braking,
        "summary_driver_takeover":    summary_driver_takeover,
        "summary_false_positive":     summary_false_positive,
        "summary_conclusion":         summary_conclusion,
    }


# ─────────────────────────────────────────────
# Main extraction
# ─────────────────────────────────────────────
def extract_mcap_data(filepath):
    """
    Parse an MCAP file and return structured event data.

    Returns a dict with keys:
        max_braking, braking_severity, velocity_at_event,
        odd, odd_event_name, mrm_triggered, scg,
        total_messages, ad_active_messages, accel_data_points,
        status, accel_timeseries, velocity_timeseries
    """
    try:
        # ── Read summary once (schema registry + channel→schema map) ────────
        with open(filepath, "rb") as f:
            reader  = make_reader(f)
            summary = reader.get_summary()

        schema_registry = _build_schema_registry(summary)

        channel_schema = {}
        if summary:
            for ch_id, ch in summary.channels.items():
                channel_schema[ch_id] = ch.schema_id

        # ── Pass 1: build AD state timeline ─────────────────────────────────
        # Must be a separate pass so that _is_active() is accurate for pass 2.
        ad_times  = []   # sorted nanosecond log-times
        ad_states = []   # state enum values, index-aligned with ad_times

        with open(filepath, "rb") as f:
            reader = make_reader(f)
            for _, channel, message in reader.iter_messages(topics=[TOPIC_AD_STATE]):
                msg_class = schema_registry.get(channel_schema.get(channel.id))
                if msg_class:
                    try:
                        msg = msg_class()
                        msg.ParseFromString(message.data)
                        val = _get_field(msg, "state.stateAutonomousDriving.enumValue")
                        if val is not None:
                            ad_times.append(message.log_time)
                            ad_states.append(int(val))
                    except Exception:
                        pass

        def _is_active(ts):
            """
            O(log n) look-up: was AD in ACTIVE state (value=48) at nanosecond timestamp ts?
            Falls back to True when no state data is available.
            """
            if not ad_times:
                return True
            idx = bisect.bisect_right(ad_times, ts) - 1
            if idx < 0:
                return False
            return ad_states[idx] == AD_STATE_ACTIVE

        # ── Pass 2: HMI state timeline ───────────────────────────────────────
        hmi_timeline = []   # (log_time, enumValue, enumText)

        with open(filepath, "rb") as f:
            reader = make_reader(f)
            for _, channel, message in reader.iter_messages(topics=[TOPIC_HMI]):
                msg_class = schema_registry.get(channel_schema.get(channel.id))
                if msg_class:
                    try:
                        msg  = msg_class()
                        msg.ParseFromString(message.data)
                        val  = _get_field(msg, "state.enumValue")
                        text = _get_field(msg, "state.enumText")
                        if val is not None:
                            hmi_timeline.append((
                                message.log_time,
                                int(val),
                                str(text).strip() if text else "",
                            ))
                    except Exception:
                        pass

        # ── Pass 3: ODD event timeline ───────────────────────────────────────
        odd_timeline = []   # (log_time, oddEventName)

        with open(filepath, "rb") as f:
            reader = make_reader(f)
            for _, channel, message in reader.iter_messages(topics=[TOPIC_ODD]):
                msg_class = schema_registry.get(channel_schema.get(channel.id))
                if msg_class:
                    try:
                        msg  = msg_class()
                        msg.ParseFromString(message.data)
                        name = _get_field(msg, "oddEventName")
                        odd_timeline.append((message.log_time, str(name).strip() if name else "N/A"))
                    except Exception:
                        pass

        # ── Pass 4: extract all signals ──────────────────────────────────────
        accel_pairs               = []   # (ns, m/s²) — ACTIVE state only
        all_accel_pairs           = []   # (ns, m/s²) — all timestamps (no AD filter)
        velocity_pairs            = []   # (ns, km/h) — ACTIVE state only
        all_velocity_pairs        = []   # (ns, km/h) — all timestamps
        driver_brake_torque_pairs = []   # (ns, Nm  ) — driver braking torque demand
        odd_events                = []   # ODD event name strings
        scg_reasons               = []   # (enumText, enumValue) for non-zero entries
        total_messages            = 0
        ad_active_messages        = 0
        errors                    = []

        with open(filepath, "rb") as f:
            reader = make_reader(f)
            for _, channel, message in reader.iter_messages():
                total_messages += 1

                active = _is_active(message.log_time)
                if active:
                    ad_active_messages += 1

                topic     = channel.topic
                msg_class = schema_registry.get(channel_schema.get(channel.id))
                if not msg_class:
                    continue

                # ── Acceleration & velocity ──────────────────────────────
                if TOPIC_ACCEL in topic:
                    try:
                        msg = msg_class()
                        msg.ParseFromString(message.data)
                        accel = _get_field(msg, "accelerationLongitudinalCOG.defaultValue")
                        vel   = _get_field(msg, "velocityVehicleCOG.defaultValue")
                        if accel is not None:
                            all_accel_pairs.append((message.log_time, float(accel)))
                        if vel is not None:
                            all_velocity_pairs.append((message.log_time, float(vel)))
                        if active:
                            if accel is not None:
                                accel_pairs.append((message.log_time, float(accel)))
                            if vel is not None:
                                velocity_pairs.append((message.log_time, float(vel)))
                    except Exception as e:
                        errors.append(f"Accel: {e}")

                # ── ODD events ───────────────────────────────────────────
                elif TOPIC_ODD in topic:
                    try:
                        msg = msg_class()
                        msg.ParseFromString(message.data)
                        name = _get_field(msg, "oddEventName")
                        if name and str(name).strip():
                            odd_events.append(str(name).strip())
                    except Exception as e:
                        errors.append(f"ODD: {e}")

                # ── Driver brake torque demand ───────────────────────────
                elif TOPIC_BRAKE_TORQUE in topic:
                    try:
                        msg = msg_class()
                        msg.ParseFromString(message.data)
                        val = _get_field(
                            msg,
                            "actualValueBrakingTorqueSumDriversChoice"
                            ".actualValueBrakingTorqueSumDriversChoice",
                        )
                        if val is not None:
                            driver_brake_torque_pairs.append((message.log_time, float(val)))
                    except Exception as e:
                        errors.append(f"BrakeTorque: {e}")

                # ── SCG safety constraints ───────────────────────────────
                elif TOPIC_SCG in topic:
                    try:
                        msg = msg_class()
                        msg.ParseFromString(message.data)
                        constraints = msg.spatialVelocityMaxConstraint
                        if constraints:
                            enum_text  = _get_field(constraints[0], "reason.enumText")
                            enum_value = _get_field(constraints[0], "reason.enumValue")
                            if enum_value is not None and int(enum_value) != 0:
                                scg_reasons.append((
                                    str(enum_text).strip() if enum_text else "Unknown",
                                    int(enum_value),
                                    message.log_time,
                                ))
                    except Exception as e:
                        errors.append(f"SCG: {e}")

        # ── Derive summary values ────────────────────────────────────────────

        # ODD: report the most frequently observed event name
        odd_event_name = "N/A"
        odd_summary    = "Not detected"
        if odd_events:
            odd_event_name, count = Counter(odd_events).most_common(1)[0]
            odd_summary = f"{odd_event_name} ({count} occurrences)"

        # SCG summary is deferred until after post_event_ts is resolved (see below)

        # Event phases: first timestamp of each HMI state transition
        event_triggered_ts = next((ts for ts, val, _ in hmi_timeline if val == HMI_STATE_EVENT), None)

        # Pre-Event: last TOR state (8, 9, or 10) that occurred before event_triggered_ts.
        # first_tor_ts = when TOR phase STARTED (used as boundary between Pre Event / Pre-Event).
        # pre_event_ts = representative sample for the Pre-Event row (last TOR msg, carries hmi_val).
        pre_event_ts      = None
        pre_event_hmi_val = None
        first_tor_ts      = None
        if event_triggered_ts is not None:
            tor_candidates = [
                (ts, val) for ts, val, _ in hmi_timeline
                if val in TOR_STATES and ts <= event_triggered_ts
            ]
            if tor_candidates:
                pre_event_ts, pre_event_hmi_val = max(tor_candidates, key=lambda x: x[0])
                first_tor_ts = min(tor_candidates, key=lambda x: x[0])[0]

        # min() rather than next() over the raw append-order hmi_timeline —
        # the list isn't guaranteed chronologically sorted (other call sites
        # explicitly re-sort it), so this must pick the chronologically
        # EARLIEST HMI=1 after event_triggered_ts, not just the first one
        # encountered in list order (which could otherwise land on an OFF
        # from a later cycle while an earlier post-event OFF is skipped, or
        # vice versa).
        post_event_candidates = [
            ts for ts, val, _ in hmi_timeline
            if val == HMI_STATE_POST_EVENT
            and event_triggered_ts is not None
            and ts > event_triggered_ts
        ]
        post_event_ts = min(post_event_candidates) if post_event_candidates else None

        # FIX 3 — cap braking window when HMI=1 was never detected
        post_event_ts_was_capped = False
        if post_event_ts is None:
            post_event_ts_was_capped = True
            if hmi_timeline:
                post_event_ts = max(t for t, _, *_ in hmi_timeline)
            elif event_triggered_ts is not None:
                post_event_ts = event_triggered_ts + int(10e9)

        # FIX 2 — SCG: filter to entries that occurred before HMI=1 (OFF)
        scg_reason_text  = "N/A"
        scg_reason_value = 0
        if scg_reasons:
            scg_filtered = (
                [(text, val) for text, val, ts in scg_reasons if ts < post_event_ts]
                if post_event_ts is not None
                else [(text, val) for text, val, ts in scg_reasons]
            )
            if scg_filtered:
                most_common_text, most_common_value = Counter(scg_filtered).most_common(1)[0][0]
                scg_reason_text  = most_common_text
                scg_reason_value = most_common_value
                unique_texts = sorted({t for t, _ in scg_filtered})
                scg_summary  = ", ".join(unique_texts)
            else:
                scg_summary = "No Reason"
        else:
            scg_summary = "No Reason"

        # ── Analysis anchor: HMI=11 when present; else highest TOR state reached ─
        anchor_ts, anchor_hmi_val, anchor_end_ts, is_fallback = \
            _get_analysis_anchor(hmi_timeline, event_triggered_ts)

        # Peak braking: standard window first; fall back to TOR phase if window is empty
        brk_window_end = (anchor_end_ts if is_fallback else post_event_ts) \
                         if anchor_ts is not None else None

        # Start of the LAST contiguous ACTIVE (HMI=3) run before HMI=11,
        # used only to cap the braking window start below. hmi_timeline
        # holds raw, repeated samples rather than deduplicated transitions
        # (e.g. 16 samples of v==3 in a row for one ACTIVE stretch), so a
        # plain min()/max() over those raw samples would return the run's
        # END, not its START. Dedupe to transitions first (one entry per
        # state change, at the FIRST sample of each run) and keep only the
        # LAST such entry — not the earliest — so a much-earlier, unrelated
        # ACTIVE segment elsewhere in the file can't hijack the cap; only
        # the run immediately preceding HMI=11 matters. Braking that begins
        # while still in ACTIVE — before any TOR escalation state even
        # fires — can start earlier than the nearest escalation-state hit,
        # so anchoring the cap here reaches back far enough to catch it.
        hmi3_start = None
        if not is_fallback and anchor_ts is not None:
            sorted_hmi_before = sorted(
                ((ts, v) for ts, v, *_ in hmi_timeline if ts < anchor_ts),
                key=lambda x: x[0],
            )
            last_val = None
            for ts, v in sorted_hmi_before:
                if v != last_val:
                    if v == 3:
                        hmi3_start = ts
                    last_val = v

        # STEP 1 — primary window: AD=48-filtered braking from
        # max(hmi3_start, hmi11_ts - 5s) through hmi1_ts. max() picks the
        # LATER of the two: the HMI=3 run-start itself when it falls within
        # 5s of HMI=11, or the flat 5s cap when that run started further
        # back than that (or no ACTIVE run was found at all). Only applies
        # when anchored on real HMI=11 — the fallback anchor already starts
        # within a TOR escalation state.
        if not is_fallback and anchor_ts is not None:
            five_s_cap = anchor_ts - int(5e9)
            brk_window_start = max(hmi3_start, five_s_cap) if hmi3_start is not None else five_s_cap
        else:
            brk_window_start = anchor_ts

        event_accel_pairs, used_braking_fallback = _extract_max_braking(
            accel_pairs,
            window_start=brk_window_start,
            window_end=brk_window_end,
            first_tor_ts=first_tor_ts,
        )

        # STEP 2 — full HMI=11 window, unfiltered: only when AD drops to
        # Init (state=2) within HMI=11 — real braking after AD disengages
        # mid-takeover gets missed by the AD=48 filter in STEP 1. Its points
        # are merged in so the final min() below picks whichever of the two
        # windows is more negative.
        if not is_fallback and anchor_ts is not None and brk_window_end is not None:
            init_in_hmi11 = next(
                (t for t, v in zip(ad_times, ad_states)
                 if anchor_ts <= t <= brk_window_end and v == 2),
                None,
            )
            if init_in_hmi11 is not None:
                full_hmi11_pairs = [
                    (t, v) for t, v in all_accel_pairs if anchor_ts <= t <= brk_window_end
                ]
                event_accel_pairs = event_accel_pairs + full_hmi11_pairs

        # STEP 3 — post-OFF window, unfiltered: braking within 1s after
        # HMI=1 (OFF) is still considered part of this event (domain rule).
        # Merged in the same way, so the final min() picks whichever of the
        # three windows is more negative.
        if not is_fallback and brk_window_end is not None:
            post_off_pairs = [
                (t, v) for t, v in all_accel_pairs
                if brk_window_end <= t <= brk_window_end + POST_OFF_BRAKING_WINDOW
            ]
            if post_off_pairs:
                event_accel_pairs = event_accel_pairs + post_off_pairs

        max_braking_raw = min(v for _, v in event_accel_pairs) if event_accel_pairs else None

        # OFF-state braking: flag only genuine spikes vs. surrounding baseline
        off_state_braking_raw = _detect_off_state_braking_spike(all_accel_pairs, post_event_ts)
        no_driver_brake_input = (
            _confirm_no_driver_brake_input(driver_brake_torque_pairs, post_event_ts)
            if off_state_braking_raw is not None else None
        )

        if max_braking_raw is None or max_braking_raw > -4.0:
            suggested_classification = "Below -4"
        elif max_braking_raw > -7.0:
            suggested_classification = "-4 to -7"
        else:
            suggested_classification = "-7 to -10"

        velocity_at_event = None
        if max_braking_raw is not None and velocity_pairs:
            t_peak            = next(t for t, v in event_accel_pairs if v == max_braking_raw)
            velocity_at_event = min(velocity_pairs, key=lambda x: abs(x[0] - t_peak))[1]

        # Phase 1: last continuous run of HMI=3 (ACTIVE) before pre_event_ts.
        # pre_pre_ts       = last HMI=3 message before pre_event_ts (used as phase timestamp)
        # pre_pre_start_ts = first message of that run (used as duration start)
        pre_pre_ts       = None
        pre_pre_start_ts = None

        if first_tor_ts is not None and hmi_timeline:
            hmi3_before = [
                (i, ts) for i, (ts, val, _) in enumerate(hmi_timeline)
                if val == 3 and ts < first_tor_ts
            ]
            if hmi3_before:
                last_idx, pre_pre_ts = hmi3_before[-1]
                # Walk backward to find the first message of this contiguous HMI=3 run
                start_idx = last_idx
                for i in range(last_idx - 1, -1, -1):
                    if hmi_timeline[i][1] == 3:
                        start_idx = i
                    else:
                        break
                pre_pre_start_ts = hmi_timeline[start_idx][0]

        # Next HMI transition after post_event_ts (used for post-event duration)
        next_after_post_ts = next(
            (ts for ts, val, _ in sorted(hmi_timeline, key=lambda x: x[0])
             if post_event_ts is not None and ts > post_event_ts and val != HMI_STATE_POST_EVENT),
            None,
        )
        if next_after_post_ts is None and hmi_timeline:
            next_after_post_ts = max(t for t, _, _ in hmi_timeline)

        # True end of the recording across every stream that can bound the
        # Post-Event Init window — HMI messages can stop being published
        # before ODD/AD ones do, so next_after_post_ts alone (HMI-only) can
        # cut the trailing Init segment off early.
        last_recording_ts = max(
            [t for t, *_ in hmi_timeline] + [t for t, _ in odd_timeline] + list(ad_times),
            default=None,
        )

        def _duration(start_ts, end_ts):
            if start_ts is None or end_ts is None:
                return None
            seconds = (end_ts - start_ts) / 1e9
            # Auto-precision: sub-100ms durations need more decimals to
            # stay visible (e.g. a genuine 0.001s AD-transition gap or a
            # 0.0002s HMI segment would otherwise round to 0.0 and look
            # identical to an exact zero-duration segment).
            if seconds <= 0:
                return 0.0
            elif seconds < 0.001:
                return round(seconds, 4)
            elif seconds < 0.1:
                return round(seconds, 3)
            return round(seconds, 2)

        def _ad_label(ts):
            if ts is None or not ad_times:
                return "N/A"
            idx = bisect.bisect_right(ad_times, ts) - 1
            if idx < 0:
                # No AD message at/before ts (e.g. HMI=3 starts a few ms
                # before the very first AD sample) — fall back to the
                # nearest AD message AFTER ts instead of reporting N/A.
                idx = 0
            val  = ad_states[idx]
            name = AD_NAMES.get(val, f"State {val}")
            return f"{name} ({val})"

        def _odd_code_at(ts, after_ts=None):
            if ts is None or not odd_timeline:
                return None
            candidates = [(t, name) for t, name in odd_timeline
                          if after_ts is None or t > after_ts]
            if not candidates:
                return None
            closest = min(candidates, key=lambda x: abs(x[0] - ts))[1]
            try:
                return int(closest)
            except (ValueError, TypeError):
                return next((k for k, v in ODD_NAMES.items() if v == closest), None)

        def _priority_odd_at(ts, window_seconds=10):
            """
            Find ODD for Event Triggered phase.
            Prioritizes DRIVER_TAKEOVER_ODD_CODES (354, 355) within window_seconds of ts
            so ODD355 is always preferred over ODD361 when both are nearby.
            Falls back to closest ODD if no takeover ODD is found in the window.
            """
            if ts is None or not odd_timeline:
                return None
            window_ns = int(window_seconds * 1e9)
            prio_candidates = []
            for t, name in odd_timeline:
                if abs(t - ts) <= window_ns:
                    try:
                        code = int(name)
                    except (ValueError, TypeError):
                        code = next(
                            (k for k, v in ODD_NAMES.items() if v == str(name).strip()), None
                        )
                    if code in DRIVER_TAKEOVER_ODD_CODES:
                        prio_candidates.append((t, code))
            if prio_candidates:
                return min(prio_candidates, key=lambda x: abs(x[0] - ts))[1]
            return _odd_code_at(ts)

        def _priority_odd_at_pre_event(ts, window_seconds=0.5):
            """
            Find the ODD code for the Pre Event (HMI=3) phase, preferring
            meaningful driver-action/event codes over known continuous background
            monitoring codes when both are present within the window.
            Falls back to plain nearest-neighbor if no non-background code exists.
            """
            if ts is None or not odd_timeline:
                return None
            window_ns = int(window_seconds * 1e9)
            candidates = [(t, name) for t, name in odd_timeline if abs(t - ts) <= window_ns]
            if not candidates:
                return _odd_code_at(ts)
            non_background = []
            for t, name in candidates:
                code = _resolve_odd_code(name)
                if code not in BACKGROUND_NOISE_ODD_CODES:
                    non_background.append((t, name))
            if non_background:
                closest = min(non_background, key=lambda x: abs(x[0] - ts))
                return _resolve_odd_code(closest[1])
            return _odd_code_at(ts)

        def _odd_at(ts, after_ts=None):
            code = _odd_code_at(ts, after_ts=after_ts)
            if code is None:
                return "N/A"
            return ODD_NAMES.get(code, f"{code} (Unknown ODD)")

        def _hmi_name(ts):
            if ts is None:
                return "N/A"
            for t, val, text in hmi_timeline:
                if t == ts:
                    return HMI_NAMES.get(val, text or f"State {val}")
            return "N/A"

        _MYT = timezone(timedelta(hours=8))

        def _ts_str(ns):
            if ns is None:
                return "N/A"
            dt = datetime.fromtimestamp(ns / 1e9, tz=timezone.utc).astimezone(_MYT)
            return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " MYT"

        def _find_sequence_start(seq_event_triggered_ts, seq_pre_pre_ts, seq_pre_pre_start_ts):
            """
            Find the timestamp that begins THIS event's continuous
            activation sequence. Skips any earlier READY/OFF cycles by
            anchoring to the most recent OFF before the event, then finding
            the LAST ACTIVE (HMI=3) strictly after that boundary — the one
            that directly precedes the TOR escalation. Earlier ACTIVE
            segments, HMI=7 (ACTIVE_UNDO_REQUEST) detours, and the
            READY/PREACTIVE/ACTIVATION_PHASE startup ramp are excluded.
            Falls back to the first READY (HMI=2) after the boundary if no
            ACTIVE is found, then to seq_pre_pre_start_ts (start of the
            contiguous HMI=3 ACTIVE run), then seq_pre_pre_ts as a last resort.
            """
            sorted_hmi_ts = sorted(hmi_timeline, key=lambda x: x[0])
            off_before_event = [
                t for t, v, _ in sorted_hmi_ts
                if v == 1 and seq_event_triggered_ts is not None and t < seq_event_triggered_ts
            ]
            boundary_ts = max(off_before_event) if off_before_event else None
            if boundary_ts is not None:
                active_candidates = [
                    t for t, v, _ in sorted_hmi_ts
                    if v == 3 and boundary_ts < t < seq_event_triggered_ts
                ]
            else:
                active_candidates = [
                    t for t, v, _ in sorted_hmi_ts
                    if v == 3 and seq_event_triggered_ts is not None and t < seq_event_triggered_ts
                ]
            if active_candidates:
                # active_candidates holds every raw HMI=3 sample, not one
                # entry per segment — max() alone would land on the last
                # sample of that run (its tail end) rather than where the
                # run started. Walk backward from the last sample through
                # the same contiguous run to find its start, so a single
                # continuous segment isn't collapsed down to an instant.
                last_active_ts = max(active_candidates)
                last_idx = next(
                    i for i, (t, v, _) in enumerate(sorted_hmi_ts)
                    if t == last_active_ts
                )
                start_idx = last_idx
                for i in range(last_idx - 1, -1, -1):
                    if sorted_hmi_ts[i][1] == 3:
                        start_idx = i
                    else:
                        break
                return sorted_hmi_ts[start_idx][0]

            if boundary_ts is not None:
                ready_candidates = [
                    t for t, v, _ in sorted_hmi_ts
                    if v == 2 and boundary_ts < t < seq_event_triggered_ts
                ]
            else:
                ready_candidates = [
                    t for t, v, _ in sorted_hmi_ts
                    if v == 2 and seq_event_triggered_ts is not None and t < seq_event_triggered_ts
                ]
            if ready_candidates:
                return min(ready_candidates)

            # No HMI=3 (ACTIVE) and no HMI=2 (READY) found at all — the
            # recording likely starts mid-activation (e.g. straight into
            # PREACTIVE/ACTIVATION_PHASE, possibly with no prior OFF in
            # the file either) with no ordinary ramp-up to anchor to.
            # Fall back to the first non-OFF HMI transition after the
            # boundary — or, if there's no boundary at all, the first
            # non-OFF transition anywhere before the event — so the Event
            # Timeline still gets a start point instead of coming back
            # empty.
            if boundary_ts is not None:
                after_boundary = [
                    t for t, v, _ in sorted_hmi_ts
                    if v != 1 and boundary_ts < t < seq_event_triggered_ts
                ]
                if after_boundary:
                    return min(after_boundary)
            else:
                all_before_event = [
                    t for t, v, _ in sorted_hmi_ts
                    if v != 1 and seq_event_triggered_ts is not None and t < seq_event_triggered_ts
                ]
                if all_before_event:
                    return min(all_before_event)

            if seq_pre_pre_start_ts is not None:
                return seq_pre_pre_start_ts
            return seq_pre_pre_ts

        def _split_row_at_ad_change(row, hmi_val, row_start_ts, row_end_ts):
            """
            If AD state changes during this HMI=11 row's time window, split
            it into sub-rows at each AD transition. The first sub-row keeps
            the "Event Triggered" phase label; later sub-rows (after AD has
            dropped out of Active) are relabeled "Post-Event" and get their
            own timestamp/ad_state/duration recomputed for their slice. The
            ODD code/name is carried over from the parent row unchanged.
            Returns a list of (row_dict, segment_start_ts, segment_end_ts)
            tuples so callers can further subdivide each segment (e.g. by
            ODD change) using its own real time bounds.
            """
            if hmi_val != 11 or row_end_ts is None:
                return [(row, row_start_ts, row_end_ts)]

            ad_transitions = sorted(
                [(t, v) for t, v in zip(ad_times, ad_states) if row_start_ts <= t <= row_end_ts],
                key=lambda x: x[0],
            )
            if not ad_transitions:
                return [(row, row_start_ts, row_end_ts)]

            segment_bounds = []
            prev_ts = row_start_ts
            prev_ad = None
            for t, ad_val in ad_transitions:
                if prev_ad is None:
                    prev_ad = ad_val
                    continue
                if ad_val != prev_ad:
                    segment_bounds.append((prev_ts, t))
                    prev_ts = t
                    prev_ad = ad_val
            segment_bounds.append((prev_ts, row_end_ts))

            if len(segment_bounds) < 2:
                return [(row, row_start_ts, row_end_ts)]

            sub_rows = []
            for i, (seg_start, seg_end) in enumerate(segment_bounds):
                sub_row = dict(row)
                sub_row["phase"]            = row["phase"] if i == 0 else "Post-Event"
                sub_row["timestamp"]        = _ts_str(seg_start)
                sub_row["ad_state"]         = _ad_label(seg_start)
                sub_row["duration_seconds"] = _duration(seg_start, seg_end)
                sub_rows.append((sub_row, seg_start, seg_end))
            return sub_rows

        def _split_row_by_odd(row, row_start_ts, row_end_ts, odd_timeline, min_duration_s=0.05, protect_first=False):
            """
            Split any row into sub-rows wherever the ODD code changes within
            the row's time window. Applies to ALL phases — Pre Event,
            Pre-Event, Event Triggered, Post-Event — since ODD can change
            mid-segment regardless of which HMI state is active.
            Segments shorter than min_duration_s are merged into a
            neighboring segment rather than shown as their own row — a
            single boundary-adjacent ODD sample landing in the last few
            milliseconds before/after an HMI transition would otherwise
            produce a spurious near-zero-duration row. A short LAST
            segment absorbs backward into the previous kept segment.
            protect_first=True (Event Triggered/HMI=11 only) additionally
            keeps the FIRST segment as its own row regardless of duration —
            a brief ODD code right before the one that actually triggered
            the event is meaningful there (it gets relabeled Pre-Event by
            the caller) and shouldn't be merged away like an ordinary
            boundary artifact. Every other caller leaves this off, since
            elsewhere a short leading/trailing sample IS just noise.
            """
            if row_end_ts is None:
                return [row]

            odd_in_window = sorted(
                [
                    (t, _resolve_odd_code(name))
                    for t, name in odd_timeline
                    if row_start_ts <= t <= row_end_ts and _resolve_odd_code(name) is not None
                ],
                key=lambda x: x[0],
            )
            if not odd_in_window:
                return [row]

            segments = []
            current_code = odd_in_window[0][1]
            current_start = row_start_ts
            for ts, code in odd_in_window[1:]:
                if code != current_code:
                    segments.append((current_start, ts, current_code))
                    current_code = code
                    current_start = ts
            segments.append((current_start, row_end_ts, current_code))

            if len(segments) == 1:
                return [row]

            # Merge segments shorter than min_duration_s into a neighbor:
            # non-final short segments carry forward into whichever
            # segment comes next; a short final segment absorbs backward
            # into the previous kept segment (extending its end/duration).
            # protect_first exempts the FIRST segment from this — it's
            # always kept as its own row regardless of duration.
            merged = []
            pending_start = None
            n = len(segments)
            for idx, (seg_start, seg_end, code) in enumerate(segments):
                start = pending_start if pending_start is not None else seg_start
                duration = (seg_end - start) / 1e9
                is_last = idx == n - 1
                is_first = idx == 0
                if duration < min_duration_s and not is_last and not (protect_first and is_first):
                    pending_start = start
                    continue
                if duration < min_duration_s and is_last and merged:
                    prev_start, _prev_end, prev_code = merged[-1]
                    merged[-1] = (prev_start, seg_end, prev_code)
                else:
                    merged.append((start, seg_end, code))
                pending_start = None

            if len(merged) == 1:
                return [row]

            sub_rows = []
            for seg_start, seg_end, odd_code in merged:
                sub_row = dict(row)
                sub_row["timestamp"]        = _ts_str(seg_start)
                sub_row["odd_code"]         = odd_code
                sub_row["odd_triggered"]    = ODD_NAMES.get(odd_code, f"{odd_code} (Unknown ODD)")
                sub_row["duration_seconds"] = _duration(seg_start, seg_end)
                sub_rows.append(sub_row)
            return sub_rows

        def _dominant_odd_in_window(window_start_ts, window_end_ts):
            """
            Returns the ODD code that appears most frequently (by sample
            count) within [window_start_ts, window_end_ts] — used for
            Post-Event so a single boundary-adjacent sample nearest to the
            window start doesn't override the code that actually dominates
            the rest of the window.
            When the window itself contains zero samples (e.g. a HMI
            phase only a few milliseconds long, shorter than the ODD
            topic's publish interval), falls back to the last ODD sample
            BEFORE the window — what was actively firing going into this
            phase — rather than nearest-in-either-direction, which can
            pick up a transient sample that actually belongs to the next
            phase just because it happens to land closer in raw time.
            """
            if window_end_ts is None:
                return _odd_code_at(window_start_ts)
            codes_in_window = [
                _resolve_odd_code(name)
                for t, name in odd_timeline
                if window_start_ts <= t <= window_end_ts and _resolve_odd_code(name) is not None
            ]
            if codes_in_window:
                return Counter(codes_in_window).most_common(1)[0][0]

            before_window = sorted(
                [
                    (t, _resolve_odd_code(name))
                    for t, name in odd_timeline
                    if t < window_start_ts and _resolve_odd_code(name) is not None
                ],
                key=lambda x: x[0],
            )
            if before_window:
                return before_window[-1][1]

            return _odd_code_at(window_start_ts)

        def _first_dominant_odd_post_event_with_duration(
            row_start_ts, row_end_ts, event_triggered_odd=None, same_odd_min_duration=0.1
        ):
            """
            Returns (odd_code, segment_duration_seconds) for the first ODD
            sub-period starting at row_start_ts — used for Post-Event so
            the code dominant across the *whole* window (which can span
            all the way to end-of-file) doesn't override what's actually
            active right after HMI=1. The only segment skipped is a brief
            residual bleed of Event Triggered's own ODD code (<
            same_odd_min_duration): a real signal, but not a new,
            meaningful ODD. Any other segment is returned regardless of
            duration — a short-but-different ODD right after HMI=1 is
            still meaningful, not noise, even if it's only a few tenths of
            a millisecond long. Returning the sub-period's own duration
            lets callers display it instead of pairing the code with a much
            longer enclosing window (e.g. the full AD=Init span).
            """
            if row_end_ts is None:
                return _odd_code_at(row_start_ts), None

            odd_in_window = sorted(
                [
                    (t, _resolve_odd_code(name))
                    for t, name in odd_timeline
                    if row_start_ts <= t <= row_end_ts and _resolve_odd_code(name) is not None
                ],
                key=lambda x: x[0],
            )
            if not odd_in_window:
                return _odd_code_at(row_start_ts), None

            segments = []
            current_code = odd_in_window[0][1]
            # Anchor the first segment to its own first ODD sample, not
            # row_start_ts — the gap between HMI=1 and whenever the first
            # ODD message actually arrives isn't part of any code's own
            # duration, and folding it into the first segment inflates it.
            current_start = odd_in_window[0][0]
            for t, code in odd_in_window[1:]:
                if code != current_code:
                    dur = (t - current_start) / 1e9
                    segments.append((current_start, t, current_code, dur))
                    current_code = code
                    current_start = t
            segments.append((
                current_start, row_end_ts, current_code,
                (row_end_ts - current_start) / 1e9,
            ))

            filtered = [
                seg for seg in segments
                if not (seg[2] == event_triggered_odd and seg[3] < same_odd_min_duration)
            ]
            if filtered:
                return filtered[0][2], filtered[0][3]

            if segments:
                return segments[0][2], segments[0][3]
            return None, None

        def _first_dominant_odd_post_event(row_start_ts, row_end_ts, event_triggered_odd=None):
            """
            Returns just the ODD code from
            _first_dominant_odd_post_event_with_duration, for callers that
            only need the code (e.g. the initial un-split Post-Event row).
            """
            code, _seg_duration = _first_dominant_odd_post_event_with_duration(
                row_start_ts, row_end_ts, event_triggered_odd=event_triggered_odd
            )
            return code

        def _auto_precision(seconds):
            # Same precision rule as _duration(): sub-100ms durations need
            # more decimals to stay visible (e.g. a genuine 0.0003s ODD
            # sub-period would otherwise round to 0.0 and look identical to
            # an exact zero-duration segment).
            if seconds is None:
                return None
            if seconds <= 0:
                return 0.0
            elif seconds < 0.001:
                return round(seconds, 4)
            elif seconds < 0.1:
                return round(seconds, 3)
            return round(seconds, 2)

        def _split_post_event_by_ad(row, row_start_ts, row_end_ts, event_triggered_odd=None):
            """
            Split a Post-Event (HMI=1) row at the point where AD State
            changes (typically Active(48) -> Init(2)) within its window.
            Some MCAPs show AD still Active at the exact HMI=1 timestamp,
            dropping to Init a few milliseconds later — each resulting
            sub-row gets its own AD-state label and the first meaningful
            ODD code for its slice (via
            _first_dominant_odd_post_event_with_duration, so the code
            dominant across the *whole remaining recording* doesn't
            override what's actually active right at the start of that
            sub-window). The trailing segment's end is extended to
            last_recording_ts when that's later than row_end_ts, since HMI
            messages can stop being published before ODD/AD ones do and
            would otherwise cut the last segment's own ODD duration short.
            event_triggered_odd is passed through so a brief residual bleed
            of Event Triggered's own ODD code is skipped rather than
            returned as this sub-row's code.
            """
            if row_end_ts is None:
                return [row]

            # Seed with the AD value actually in effect AT row_start_ts
            # (step-function lookup, same convention as _ad_label/_is_active)
            # — its defining sample may have arrived before the window even
            # started, so a plain row-window filter alone can miss it.
            idx = bisect.bisect_right(ad_times, row_start_ts) - 1
            if idx < 0:
                return [row]
            current_ad = ad_states[idx]

            ad_in_window = sorted(
                [(t, v) for t, v in zip(ad_times, ad_states) if row_start_ts < t <= row_end_ts],
                key=lambda x: x[0],
            )

            segments = []
            current_start = row_start_ts
            for t, ad_val in ad_in_window:
                if ad_val != current_ad:
                    segments.append((current_start, t, current_ad))
                    current_ad = ad_val
                    current_start = t
            segments.append((current_start, row_end_ts, current_ad))

            if last_recording_ts is not None and last_recording_ts > row_end_ts:
                final_start, _final_end, final_ad = segments[-1]
                segments[-1] = (final_start, last_recording_ts, final_ad)

            # No early-return shortcut for the single-segment case: AD
            # almost always transitions to Init exactly once and then stays
            # there for the rest of the recording, so "only one AD segment"
            # is the COMMON case here, not an edge case. It still needs to
            # go through the loop below so this segment's ODD code gets its
            # own sub-period duration (via
            # _first_dominant_odd_post_event_with_duration) instead of the
            # full AD-span duration the original un-split row carries.

            sub_rows = []
            for seg_start, seg_end, _ad_val in segments:
                sub_row = dict(row)
                sub_row["timestamp"] = _ts_str(seg_start)
                sub_row["ad_state"]  = _ad_label(seg_start)
                odd_code, odd_duration = _first_dominant_odd_post_event_with_duration(
                    seg_start, seg_end, event_triggered_odd=event_triggered_odd
                )
                if odd_code is not None:
                    sub_row["odd_code"]      = odd_code
                    sub_row["odd_triggered"] = ODD_NAMES.get(odd_code, f"{odd_code} (Unknown ODD)")
                # Show the ODD sub-period's own duration rather than the
                # full AD-segment span it was found inside (which can run
                # all the way to end-of-file).
                if odd_duration is not None:
                    sub_row["duration_seconds"] = _auto_precision(odd_duration)
                else:
                    sub_row["duration_seconds"] = _duration(seg_start, seg_end)
                sub_rows.append(sub_row)
            return sub_rows

        def _build_event_table(seq_pre_pre_ts, seq_pre_pre_start_ts, seq_event_triggered_ts,
                               seq_post_event_ts, seq_next_after_post_ts,
                               seq_pre_event_tor_odd_code):
            """
            Unified dynamic Event Timeline builder.
            Start point: READY that begins this event's activation cycle
            (after the most recent prior OFF), else seq_pre_pre_start_ts fallback.
            Walks every distinct HMI transition through post_event_ts,
            applying specialised ODD lookups for key phases.
            """
            if not hmi_timeline or seq_post_event_ts is None:
                return []
            sorted_hmi = sorted(hmi_timeline, key=lambda x: x[0])
            sequence_start_ts = _find_sequence_start(
                seq_event_triggered_ts, seq_pre_pre_ts, seq_pre_pre_start_ts
            )
            if sequence_start_ts is None:
                return []
            transitions = []
            last_val = None
            for t, val, _ in sorted_hmi:
                if t < sequence_start_ts:
                    continue
                if t > seq_post_event_ts:
                    break
                if val != last_val:
                    transitions.append((t, val))
                    last_val = val
            rows = []
            for i, (t, hmi_val) in enumerate(transitions):
                if i + 1 < len(transitions):
                    next_t = transitions[i + 1][0]
                else:
                    next_t = seq_next_after_post_ts
                # Event Triggered's own ODD, from the row already appended
                # earlier in this same transitions walk — used by the
                # HMI=1 branch (here and in the AD-split loop below) to
                # skip a brief residual bleed of that same code.
                event_triggered_odd = next(
                    (r.get("odd_code") for r in rows if r.get("phase") == "Event Triggered"),
                    None,
                )
                if hmi_val == 3:
                    odd_code = _odd_code_at(seq_pre_pre_start_ts)
                elif hmi_val == 11:
                    odd_code = _priority_odd_at(t)
                elif hmi_val == 1:
                    odd_code = _first_dominant_odd_post_event(t, next_t, event_triggered_odd=event_triggered_odd)
                else:
                    # Pre-Event escalation states (4/8/9/10) and any
                    # intermediate states (2/5/6/7/12/13/14, etc.) — use
                    # the dominant code in the window rather than plain
                    # nearest-timestamp, which can pick a trailing sample
                    # from the tail of the *previous* phase over the code
                    # that actually owns this row's own window.
                    odd_code = _dominant_odd_in_window(t, next_t)
                odd_triggered = ODD_NAMES.get(odd_code, f"{odd_code} (Unknown ODD)") if odd_code is not None else "N/A"
                phase_label = _get_phase_label(hmi_val, t, first_tor_ts)
                row = {
                    "phase":            phase_label,
                    "timestamp":        _ts_str(t),
                    "hmi_state":        HMI_STATE_NAMES.get(hmi_val, f"Unknown({hmi_val})"),
                    "hmi_value":        hmi_val,
                    "ad_state":         _ad_label(t),
                    "odd_triggered":    odd_triggered,
                    "odd_code":         odd_code,
                    "duration_seconds": _duration(t, next_t),
                }
                for seg_row, seg_start, seg_end in _split_row_at_ad_change(row, hmi_val, t, next_t):
                    if hmi_val == 1:
                        # Post-Event: split at the AD Active(48)->Init(2)
                        # boundary if present, but NOT by ODD — many ODD
                        # codes fire rapidly during system OFF/recovery,
                        # so ODD splitting isn't meaningful for this phase.
                        post_event_rows = _split_post_event_by_ad(
                            seg_row, seg_start, seg_end, event_triggered_odd=event_triggered_odd
                        )
                        # Drop rows that reduce to a brief residual bleed
                        # of Event Triggered's own ODD (e.g. the Active(48)
                        # blip that lingers for a moment right after HMI
                        # goes OFF) — a real signal, but not analytically
                        # meaningful. Rows with a genuinely different ODD
                        # are kept regardless of their own duration; only
                        # matching _first_dominant_odd_post_event_with_
                        # duration's fallback-returns-the-blip-anyway case
                        # (when an entire AD segment reduces to just that
                        # blip) needs catching here.
                        filtered_post_event_rows = [
                            r for r in post_event_rows
                            if not (
                                r.get("odd_code") == event_triggered_odd
                                and r.get("duration_seconds") is not None
                                and r.get("duration_seconds") < 0.1
                            )
                        ]
                        # Safety fallback: if every sub-row got filtered
                        # out, the Event Timeline would end up with no
                        # Post-Event row at all — keep the longest-duration
                        # one instead of losing the phase entirely.
                        if not filtered_post_event_rows and post_event_rows:
                            filtered_post_event_rows = [
                                max(post_event_rows, key=lambda r: r.get("duration_seconds") or 0)
                            ]
                        rows.extend(filtered_post_event_rows)
                    elif hmi_val == 3:
                        # Pre Event: when ODD oscillates within the ACTIVE
                        # segment, only the LAST sub-segment (the ODD
                        # active immediately before TOR escalation begins)
                        # is meaningful — keep that one row, drop earlier
                        # oscillations.
                        split = _split_row_by_odd(seg_row, seg_start, seg_end, odd_timeline)
                        if split:
                            rows.append(split[-1])
                    elif hmi_val == 11:
                        # Event Triggered: any ODD sub-segment before the
                        # first Prio3 (strong-braking/strong-steering)
                        # code is really still Pre-Event — the takeover
                        # trigger hasn't fired yet. Relabel only those
                        # leading sub-rows; everything from the first
                        # Prio3 segment onward keeps whatever phase it
                        # already had (e.g. "Post-Event" if this segment
                        # is the AD=Init portion from _split_row_at_ad_change).
                        split = _split_row_by_odd(seg_row, seg_start, seg_end, odd_timeline, protect_first=True)
                        prio3_idx = next(
                            (i for i, r in enumerate(split) if r.get("odd_code") in PRIO3_ODD_CODES),
                            0,
                        )
                        # Event Triggered's own ODD, from the row already
                        # appended earlier in this same HMI=11 sequence —
                        # used below to drop residual signal bleed.
                        event_triggered_odd = next(
                            (r.get("odd_code") for r in rows if r.get("phase") == "Event Triggered"),
                            None,
                        )
                        for i, sub_row in enumerate(split):
                            if i < prio3_idx:
                                sub_row["phase"] = "Pre-Event"
                                rows.append(sub_row)
                                continue
                            if (
                                sub_row.get("phase") != "Event Triggered"
                                and sub_row.get("odd_code") == event_triggered_odd
                                and sub_row.get("duration_seconds") is not None
                                and sub_row.get("duration_seconds") < 0.1
                            ):
                                # Residual signal bleed: AD drops to Init
                                # within the same HMI=11 window (a separate
                                # _split_row_by_odd call on that segment),
                                # but this sub-row still carries Event
                                # Triggered's own ODD code for only a
                                # moment — not a new, meaningful ODD. The
                                # phase check (rather than an index check
                                # against this call's own local prio3_idx)
                                # is what actually protects the real Event
                                # Triggered row, since prio3_idx is
                                # recomputed fresh per AD segment and
                                # doesn't identify it across segments.
                                continue
                            rows.append(sub_row)
                    else:
                        rows.extend(_split_row_by_odd(seg_row, seg_start, seg_end, odd_timeline))
            return rows

        # ── Velocity window anchored at event start (HMI=11 or fallback TOR state) ──
        # v1 = peak velocity while AD=48, in [first_tor_ts - 5s, hmi1_ts] —
        # the speed right before deceleration begins, not just whatever
        # sample happens to land at hmi11_ts.
        # v2 = absolute minimum in [anchor_ts, hmi1_ts + 5s].
        velocity_at_trigger = None
        min_velocity_after  = None
        if anchor_ts is not None and all_velocity_pairs:
            vel_window_end = anchor_end_ts if is_fallback else post_event_ts
            vel_kmh_pairs  = [(t, _to_kmh(v)) for t, v in all_velocity_pairs]
            t_window_end   = (vel_window_end + int(5e9)) if vel_window_end is not None \
                             else (anchor_ts + int(10e9))

            v1_window_start = (first_tor_ts - int(5e9)) if first_tor_ts is not None else anchor_ts
            v1_window_end   = vel_window_end if vel_window_end is not None else (anchor_ts + int(10e9))
            v1_pairs = [
                (t, v) for t, v in vel_kmh_pairs
                if v1_window_start <= t <= v1_window_end and _is_active(t)
            ]
            v1 = max(v for _, v in v1_pairs) if v1_pairs else None
            if v1 is not None:
                velocity_at_trigger = round(v1, 2)

            window_vels = [(t, v) for t, v in vel_kmh_pairs if anchor_ts <= t <= t_window_end]
            if not window_vels:
                window_vels = [(t, v) for t, v in vel_kmh_pairs
                               if anchor_ts - int(2e9) <= t <= t_window_end]
            if window_vels:
                v2 = min(v for _, v in window_vels)
                min_velocity_after = round(v2, 2)

        if velocity_at_trigger is not None and velocity_at_trigger <= 2:
            velocity_display     = "Vehicle Standstill"
            min_velocity_display = "Vehicle Standstill"
        else:
            velocity_display     = f"{velocity_at_trigger} km/h" if velocity_at_trigger is not None else "N/A"
            min_velocity_display = f"{min_velocity_after} km/h" if min_velocity_after is not None else "N/A"

        # ── ODD code at event triggered phase ────────────────────────────────
        event_odd_code = _priority_odd_at(event_triggered_ts)

        # True when codes 354/355 appear nowhere in the ODD timeline —
        # meaning the takeover trigger was not available in any state machine.
        odd_code_not_in_state_machine = not _has_354_355_anywhere(odd_timeline)

        # Pre-Event (TOR) ODD code — anchor for the Pre Event (HMI=3) predecessor lookup
        _pre_event_tor_odd_code = _odd_code_at(pre_event_ts)

        event_table = _build_event_table(
            pre_pre_ts, pre_pre_start_ts, event_triggered_ts, post_event_ts,
            next_after_post_ts, _pre_event_tor_odd_code,
        )
        event_table = _mark_core_extended(event_table)

        # Extraction status — friendly messages for known failure modes
        if not hmi_timeline or event_triggered_ts is None:
            status = ("No event timeline detected. MCAP file may not contain "
                      "a complete ADAS event sequence.")
        elif not accel_pairs and not PROTOBUF_AVAILABLE:
            status = ("Unable to decode MCAP file format. "
                      "File may be corrupted or use unsupported encoding.")
        elif not accel_pairs:
            status = ("No braking data found within the event window. "
                      "Check if AD State was Active during the event.")
        elif not PROTOBUF_AVAILABLE:
            status = "Warning: Limited extraction (install protobuf)"
        else:
            status = "Extraction Completed"

        _odd_trigger_name = ODD_NAMES.get(event_odd_code, f"{event_odd_code} (Unknown ODD)") if event_odd_code is not None else "N/A"
        result = {
            "max_braking":              f"{max_braking_raw:.4f} m/s²" if max_braking_raw is not None else "Not detected",
            "max_braking_raw":          max_braking_raw,
            "braking_severity":         _classify_braking(max_braking_raw),
            "velocity_at_event":        f"{round(velocity_at_event, 1)} km/h" if velocity_at_event is not None else "N/A",
            "velocity_at_trigger":      velocity_display,
            "velocity_at_trigger_kmh":  velocity_at_trigger,
            "min_velocity_after":       min_velocity_display,
            "min_velocity_after_kmh":   min_velocity_after,
            "odd":                      odd_summary,
            "odd_trigger":              f"{event_odd_code} - {_odd_trigger_name}" if event_odd_code is not None else "N/A",
            "odd_event_name":           ODD_NAMES.get(event_odd_code, f"{event_odd_code} (Unknown ODD)") if event_odd_code is not None else odd_event_name,
            "event_odd_code":           event_odd_code,
            "pre_event_hmi_val":        pre_event_hmi_val,
            "event_type":           classify_event_type(event_odd_code),
            "suggested_classification": suggested_classification,
            "mrm_triggered":        "Yes" if event_odd_code in MRM_ODD_CODES else "No",
            "scg":                  scg_summary,
            "scg_reason_text":      scg_reason_text,
            "scg_reason_value":     scg_reason_value,
            "total_messages":       total_messages,
            "ad_active_messages":   ad_active_messages,
            "accel_data_points":    len(accel_pairs),
            "status":               status,
            "errors":               errors[:5],
            "accel_timeseries":     _normalize_timeseries(accel_pairs),
            "velocity_timeseries":  _normalize_timeseries(velocity_pairs),
            "event_table":          event_table,
            "analysis_anchor_note": (
                f"No HAF_TAF_TOR (HMI=11) detected. Analysis based on "
                f"highest escalation reached: TOR "
                f"{'Red' if anchor_hmi_val == 10 else 'Yellow' if anchor_hmi_val == 9 else 'Blue'}."
            ) if is_fallback else None,
            "braking_window_note": (
                "Maximum braking computed from the TOR Red phase because the AD system "
                "had already transitioned out of Active state by the time HAF_TAF_TOR triggered."
            ) if used_braking_fallback else None,
            "post_event_note": (
                "HMI=1 (OFF) was not detected after HAF_TAF_TOR. "
                "Braking window capped at last HMI message timestamp."
            ) if post_event_ts_was_capped else None,
            "odd_code_not_in_state_machine": odd_code_not_in_state_machine,
            "off_state_braking_raw":         off_state_braking_raw,
            "no_driver_brake_input":         no_driver_brake_input,
        }
        result["analysis_summary"] = generate_analysis_summary(result)
        return result

    except FileNotFoundError:
        return _error_result(
            "MCAP file could not be located. Please re-upload the file.")
    except Exception as e:
        msg = str(e)
        if any(k in msg.lower() for k in ("protobuf", "decode", "parse", "parsefrom")):
            return _error_result(
                "Unable to decode MCAP file format. "
                "File may be corrupted or use unsupported encoding.")
        return _error_result(msg)
