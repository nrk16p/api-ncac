from sqlalchemy import Column, Integer, String, Date, DateTime, Float, Text, ForeignKey
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import relationship
from database import Base


class SafetyTalk(Base):
    __tablename__ = "safety_talk"

    safety_talk_id = Column(Integer, primary_key=True, index=True)
    noted = Column(Text)
    upload_url = Column(Text)


class DrugTest(Base):
    __tablename__ = "drug_test"

    drug_test_id = Column(Integer, primary_key=True, index=True)
    alcohol = Column(Float)
    alcohol_attachment = Column(Text)
    amfetamin_2 = Column(String)
    amfetamin_attachment = Column(String)


class PPETest(Base):
    __tablename__ = "ppe_test"

    ppe_test_id = Column(Integer, primary_key=True, index=True)

    shirt_check = Column(String, nullable=True)
    shirt_size = Column(String, nullable=True)
    boot_check = Column(String, nullable=True)
    boot_size = Column(String, nullable=True)
    ppe_attachment = Column(String, nullable=True)

class VehicleInspect(Base):
    __tablename__ = "vehicle_inspect"

    vehicle_inspect_id = Column(Integer, primary_key=True, index=True)

    around_check_side_1 = Column(String)
    around_check_side_2 = Column(String)
    around_check_side_3 = Column(String)
    around_check_side_4 = Column(String)

    around_check_attachment = Column(ARRAY(String))

    equiement_check = Column(String)
    cockpit_check = Column(String)
    cockpit_check_attachment = Column(String)



class InspectionTask(Base):
    __tablename__ = "inspection_task"

    inspection_task_id = Column(String, primary_key=True, index=True)

    trainer_id = Column(String)    
    client_name = Column(String)
    plant_code = Column(String)

    plan_date = Column(Date)
    action_date = Column(DateTime)

    inspection_task_status = Column(String)

    drivers = relationship("InspectionTaskDriver", back_populates="task")

class InspectionTaskDriver(Base):
    __tablename__ = "inspection_task_driver"

    inspection_task_driver_id = Column(String, primary_key=True, index=True)

    inspection_task_id = Column(
        String,
        ForeignKey("inspection_task.inspection_task_id")
    )

    driver_id = Column(String)      # changed to string
    truck_id = Column(String)       # changed to string
    truck_number = Column(String)   # new field

    truck_type = Column(String)

    drug_test_id = Column(
        Integer,
        ForeignKey("drug_test.drug_test_id"),
        nullable=True
    )

    ppe_test_id = Column(
        Integer,
        ForeignKey("ppe_test.ppe_test_id"),
        nullable=True
    )

    vehicle_inspect_id = Column(
        Integer,
        ForeignKey("vehicle_inspect.vehicle_inspect_id"),
        nullable=True
    )

    inspection_task_driver_status = Column(String)

    task = relationship("InspectionTask", back_populates="drivers")

    drug_test = relationship("DrugTest")
    ppe_test = relationship("PPETest")
    vehicle_inspect = relationship("VehicleInspect")