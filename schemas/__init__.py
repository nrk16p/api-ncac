# ============================================================
# USER
# ============================================================
from .user_schema import UserCreate, UserResponse

# ============================================================
# POSITION
# ============================================================
from .position_schema import (
    PositionCreate,
    PositionResponse,
    PositionLevelCreate,
    PositionLevelResponse,
)

# ============================================================
# CASE REPORT
# ============================================================
from .case_schema import (
    CaseReportCreate,
    CaseReportUpdate,
    CaseReportResponse,
    CaseReportDocSchema,
)

# ============================================================
# ACCIDENT CASE
# ============================================================
from .accident_schema import (
    AccidentCaseCreate,
    AccidentCaseUpdate,
    AccidentCaseResponse,
    AccidentCaseDocSchema,
    AccidentCaseDocData,  # ✅ add this line
)

# ============================================================
# MASTER / SUPPORTING SCHEMAS
# ============================================================
from .department_schema import DepartmentCreate, DepartmentResponse
from .site_schema import SiteCreate, SiteResponse
from .client_schema import ClientCreate, ClientResponse
from .location_schema import (
    ProvinceCreate,
    ProvinceResponse,
    DistrictCreate,
    DistrictResponse,
    SubDistrictCreate,
    SubDistrictResponse,
    LocationResponse,
)

# ============================================================
# CASE REPORT INVESTIGATION
# ============================================================
from .case_report_investigate_schema import (
    CaseReportInvestigateCreate,
    CaseReportInvestigateUpdate,
    CaseReportInvestigateOut,
)
from .corrective_action_schema import CorrectiveActionItem
from .master_schema import (
    MasterDriverCreate,
    MasterDriverResponse,
    DriverRoleCreate,
    DriverRoleResponse,
    MasterCauseCreate,
    MasterCauseResponse,
    VehicleCreate,
    VehicleResponse,
)