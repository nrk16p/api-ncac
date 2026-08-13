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

# complaint_master ต้องอยู่ใน metadata ตอน main.py เรียก Base.metadata.create_all()
# (บรรทัดนั้นรันก่อน import routes ตารางที่รู้จักผ่าน routes อย่างเดียวจึงไม่ถูกสร้าง)
from .complaint_master import ComplaintMaster
