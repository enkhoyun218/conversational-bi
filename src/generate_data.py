"""Generate a synthetic public-pension / disability-retirement dataset.

Produces three related CSVs under data/:
  - departments.csv  (dimension table)
  - employees.csv    (the covered population)
  - claims.csv        (a subset of employees who filed a disability claim)

Seeded for reproducibility -- rerunning this script always produces the
same data, which matters when you're demoing the app and want stable
query results.
"""
import os
import random
from datetime import date, timedelta

import pandas as pd

RANDOM_SEED = 42
random.seed(RANDOM_SEED)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

DEPARTMENTS = [
    "Police",
    "Fire",
    "Public Works",
    "Parks and Recreation",
    "Health Services",
    "Transportation",
    "Sanitation",
    "Corrections",
    "Education Support",
    "Administration",
]

INJURY_TYPES = [
    "Back Injury",
    "Knee Injury",
    "Cardiac",
    "PTSD",
    "Hearing Loss",
    "Repetitive Strain",
    "Respiratory",
    "Vision Loss",
    "Shoulder Injury",
    "Other",
]

N_EMPLOYEES = 400
CLAIM_RATE = 0.35    # share of employees who have ever filed a claim
PENDING_RATE = 0.15  # share of claims still open (no application_end yet)


def generate_departments() -> pd.DataFrame:
    return pd.DataFrame({
        "department_id": range(1, len(DEPARTMENTS) + 1),
        "department_name": DEPARTMENTS,
    })


def generate_employees(departments: pd.DataFrame) -> pd.DataFrame:
    rows = []
    department_ids = departments["department_id"].tolist()
    for employee_id in range(1, N_EMPLOYEES + 1):
        age = random.randint(22, 70)
        # service credit can't exceed a plausible working lifetime (started at 18)
        max_service = min(age - 18, 40)
        service_credit = round(random.uniform(0.5, max_service), 1)
        rows.append({
            "employee_id": employee_id,
            "department_id": random.choice(department_ids),
            "service_credit": service_credit,
            "age": age,
        })
    return pd.DataFrame(rows)


def generate_claims(employees: pd.DataFrame) -> pd.DataFrame:
    n_claimants = int(len(employees) * CLAIM_RATE)
    claimant_ids = random.sample(employees["employee_id"].tolist(), n_claimants)

    rows = []
    claim_id = 1
    for employee_id in claimant_ids:
        # most claimants file once; a few file a second claim later in their career
        n_claims = random.choices([1, 2], weights=[0.85, 0.15])[0]
        for _ in range(n_claims):
            start = date(2018, 1, 1) + timedelta(days=random.randint(0, 365 * 7))
            is_pending = random.random() < PENDING_RATE
            end = None if is_pending else start + timedelta(days=random.randint(14, 400))
            rows.append({
                "claim_id": claim_id,
                "employee_id": employee_id,
                "injury_type": random.choice(INJURY_TYPES),
                "application_start": start,
                "application_end": end,
            })
            claim_id += 1
    return pd.DataFrame(rows)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    departments = generate_departments()
    employees = generate_employees(departments)
    claims = generate_claims(employees)

    departments.to_csv(os.path.join(DATA_DIR, "departments.csv"), index=False)
    employees.to_csv(os.path.join(DATA_DIR, "employees.csv"), index=False)
    claims.to_csv(os.path.join(DATA_DIR, "claims.csv"), index=False)

    print(f"departments.csv: {len(departments)} rows")
    print(f"employees.csv:   {len(employees)} rows")
    print(f"claims.csv:      {len(claims)} rows")


if __name__ == "__main__":
    main()
