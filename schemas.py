from pydantic import BaseModel

class RegisterRequest(BaseModel):
    username: str
    password: str
    firstname: str
    lastname: str
    employee_id: str | None = None
    department_id: int | None = None
    site_id: int | None = None
    position_id: int | None = None