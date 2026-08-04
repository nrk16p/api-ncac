from .user_model import User, Position, Department, Site, PositionLevel ,Client
from .master_model import MasterDriver, MasterCause, DriverRole, Vehicle
from .location_model import Province, District, SubDistrict, Location
from .case_report_model import (
    CaseReport,
    CaseProduct,
    CaseReportInvestigate,
    CaseReportCorrectiveAction,
    CaseReportDoc,
)
from .accident_case_model import AccidentCase, AccidentCaseDoc
from .accident_case_investigate_model import (
    AccidentCaseInvestigate,
    AccidentCaseInvestigateWhy,
    AccidentCaseInvestigateRootCause,
    AccidentCaseInvestigateMeasure,
    AccidentCaseInvestigateInvestigator,
)
from .drivingdistance_model import DrivingDistance
