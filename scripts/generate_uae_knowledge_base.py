"""Generate the synthetic UAE knowledge base under knowledge_base/ and validate its internal consistency.

    uv run python scripts/generate_uae_knowledge_base.py           # write files
    uv run python scripts/generate_uae_knowledge_base.py --check   # fail if files are stale or inconsistent

EVERYTHING PRODUCED HERE IS FICTIONAL. The structure follows UAE health-insurance practice (DHA/DOH mandated
minimum cover, tiered networks, co-payments, pre-authorisation thresholds, waiting periods), but every figure,
name, code, licence number and policy number is invented. Procedure codes use a synthetic `SP-#####` scheme and
deliberately do not correspond to CPT codes or their meanings.

The generator is the source of truth: it computes per-tier pre-authorisation flags from each procedure's rule and
the tier thresholds, so the JSON cannot drift out of consistency by hand-editing.
"""

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

OUT = Path(__file__).resolve().parents[1] / "knowledge_base"

DISCLAIMER = (
    "SYNTHETIC TEST DATA. Fictional insurer, plans, providers, members and procedure codes. Structurally modelled "
    "on UAE health-insurance regulation (DHA/DOH) for demonstration only. Not real policy data and not valid for "
    "any real authorisation decision."
)

TIER_ORDER = ["BASIC", "ENHANCED", "COMPREHENSIVE", "EXECUTIVE"]
# Policy periods are derived from the year the catalogue is generated, so members never fall out of cover.
GENERATED_FOR = date.today()

# --------------------------------------------------------------------------- tiers

TIERS: list[dict[str, Any]] = [
    {
        "tier_id": "BASIC",
        "name": "Basic",
        "product_name": "Sawt Assurance Basic (Essential Benefits equivalent)",
        "regulatory_basis": "Modelled on the DHA-mandated Essential Benefits Plan minimum cover. Figures fictional.",
        "eligibility_note": "Mandatory minimum cover for employees earning up to AED 4,000 per month and their "
                            "domestic workers.",
        "annual_limit_aed": 150000,
        "sub_limits_aed": {
            "inpatient": 150000, "outpatient": 30000, "pharmacy": 1500, "maternity": 7000,
            "dental": 0, "optical": 0, "mental_health": 0,
        },
        "network": {"network_id": "BASIC_NETWORK", "name": "Basic Network",
                    "geography": "Dubai and Northern Emirates", "out_of_network_cover": False},
        "co_payments_percent": {"outpatient_consultation": 20, "diagnostics": 20, "inpatient": 20,
                                "pharmacy": 30, "emergency": 0},
        "co_payment_caps_aed": {"outpatient_consultation": 50, "inpatient_per_encounter": 500,
                                "annual_out_of_pocket": 4000},
        "pre_authorisation_threshold_aed": 1000,
        "waiting_periods_months": {"maternity": 12, "pre_existing_conditions": 6, "chronic_conditions": 6,
                                   "dental": 0, "optical": 0},
        "deductible_aed": 0,
        "notes": "Emergency treatment is never subject to pre-authorisation; notify within 24 hours of admission.",
    },
    {
        "tier_id": "ENHANCED",
        "name": "Enhanced",
        "product_name": "Sawt Assurance Enhanced",
        "regulatory_basis": "Above-minimum commercial plan. Figures fictional.",
        "eligibility_note": "Salaried employees above the mandated minimum band; dependants may be added.",
        "annual_limit_aed": 500000,
        "sub_limits_aed": {
            "inpatient": 500000, "outpatient": 75000, "pharmacy": 5000, "maternity": 15000,
            "dental": 2500, "optical": 1200, "mental_health": 10000,
        },
        "network": {"network_id": "ENHANCED_NETWORK", "name": "Enhanced Network",
                    "geography": "United Arab Emirates", "out_of_network_cover": False},
        "co_payments_percent": {"outpatient_consultation": 20, "diagnostics": 15, "inpatient": 10,
                                "pharmacy": 20, "emergency": 0},
        "co_payment_caps_aed": {"outpatient_consultation": 50, "inpatient_per_encounter": 500,
                                "annual_out_of_pocket": 7500},
        "pre_authorisation_threshold_aed": 2500,
        "waiting_periods_months": {"maternity": 12, "pre_existing_conditions": 6, "chronic_conditions": 6,
                                   "dental": 6, "optical": 6},
        "deductible_aed": 0,
        "notes": "Dental and optical benefits carry a six-month waiting period from policy inception.",
    },
    {
        "tier_id": "COMPREHENSIVE",
        "name": "Comprehensive",
        "product_name": "Sawt Assurance Comprehensive",
        "regulatory_basis": "Above-minimum commercial plan. Figures fictional.",
        "eligibility_note": "Mid-to-senior staff and their dependants.",
        "annual_limit_aed": 1000000,
        "sub_limits_aed": {
            "inpatient": 1000000, "outpatient": 150000, "pharmacy": 10000, "maternity": 30000,
            "dental": 6000, "optical": 2000, "mental_health": 25000,
        },
        "network": {"network_id": "COMPREHENSIVE_NETWORK", "name": "Comprehensive Network",
                    "geography": "United Arab Emirates, with GCC emergency cover", "out_of_network_cover": False},
        "co_payments_percent": {"outpatient_consultation": 10, "diagnostics": 10, "inpatient": 0,
                                "pharmacy": 10, "emergency": 0},
        "co_payment_caps_aed": {"outpatient_consultation": 50, "inpatient_per_encounter": 0,
                                "annual_out_of_pocket": 10000},
        "pre_authorisation_threshold_aed": 5000,
        "waiting_periods_months": {"maternity": 12, "pre_existing_conditions": 3, "chronic_conditions": 3,
                                   "dental": 3, "optical": 3},
        "deductible_aed": 0,
        "notes": "Chronic-condition management is covered after a three-month waiting period.",
    },
    {
        "tier_id": "EXECUTIVE",
        "name": "Executive",
        "product_name": "Sawt Assurance Executive",
        "regulatory_basis": "Top commercial tier. Figures fictional.",
        "eligibility_note": "Executive grades and their dependants; worldwide cover excluding the USA.",
        "annual_limit_aed": 3000000,
        "sub_limits_aed": {
            "inpatient": 3000000, "outpatient": 400000, "pharmacy": 25000, "maternity": 60000,
            "dental": 15000, "optical": 4000, "mental_health": 60000,
        },
        "network": {"network_id": "EXECUTIVE_NETWORK", "name": "Executive Network",
                    "geography": "Worldwide excluding the USA", "out_of_network_cover": True},
        "co_payments_percent": {"outpatient_consultation": 0, "diagnostics": 0, "inpatient": 0,
                                "pharmacy": 0, "emergency": 0},
        "co_payment_caps_aed": {"outpatient_consultation": 0, "inpatient_per_encounter": 0,
                                "annual_out_of_pocket": 0},
        "pre_authorisation_threshold_aed": 10000,
        "waiting_periods_months": {"maternity": 12, "pre_existing_conditions": 0, "chronic_conditions": 0,
                                   "dental": 0, "optical": 0},
        "deductible_aed": 0,
        "notes": "Out-of-network treatment is reimbursed at 80% of the agreed tariff after pre-authorisation.",
    },
]

# --------------------------------------------------------------------------- procedures
# (code, name, category, specialty, cost_aed, preauth_rule, min_tier, waiting_months, exclusions, ambiguity)
# preauth_rule: ALWAYS | ABOVE_TIER_THRESHOLD | NEVER
# min_tier: lowest tier that covers it, or NONE for an exclusion on every tier.

P = tuple[str, str, str, str, int, str, str, int, list[str], dict[str, str] | None]

PROCEDURES: list[P] = [
    # --- diagnostic
    ("SP-10010", "Chest X-ray, two views", "diagnostic", "Radiology", 180, "NEVER", "BASIC", 0, [], None),
    ("SP-10020", "Ultrasound, abdomen complete", "diagnostic", "Radiology", 520, "ABOVE_TIER_THRESHOLD", "BASIC", 0, [], None),
    ("SP-10030", "CT scan, head, without contrast", "diagnostic", "Radiology", 1450, "ABOVE_TIER_THRESHOLD", "BASIC", 0, [], None),
    ("SP-10040", "MRI, brain, without contrast", "diagnostic", "Radiology", 2600, "ALWAYS", "BASIC", 0, [], None),
    ("SP-10050", "MRI, knee, without contrast", "diagnostic", "Radiology", 2400, "ALWAYS", "BASIC", 0,
     ["Not covered when requested solely for screening without clinical findings"], None),
    ("SP-10060", "PET-CT, whole body", "diagnostic", "Nuclear Medicine", 9800, "ALWAYS", "ENHANCED", 0, [], None),
    ("SP-10070", "Echocardiogram, transthoracic", "diagnostic", "Cardiology", 650, "ABOVE_TIER_THRESHOLD", "BASIC", 0, [], None),
    ("SP-10090", "Complete blood count", "diagnostic", "Laboratory", 60, "NEVER", "BASIC", 0, [], None),
    ("SP-10110", "Hereditary cancer multigene panel", "diagnostic", "Genetics", 7500, "ALWAYS", "COMPREHENSIVE", 0,
     ["Not covered for population screening without a qualifying family history"],
     {"escalation_rule_id": "ESC-005",
      "reason": "Genetic panels are not listed individually in the benefit schedule; eligibility depends on family "
                "history criteria that the schedule does not define."}),
    ("SP-10120", "Sleep study, attended polysomnography", "diagnostic", "Pulmonology", 3200, "ALWAYS", "ENHANCED", 0, [],
     {"escalation_rule_id": "ESC-004",
      "reason": "Attended and home sleep studies are frequently coded interchangeably; the tariff differs and the "
                "submitted code often disagrees with the clinical notes."}),
    ("SP-10130", "Colonoscopy, diagnostic", "diagnostic", "Gastroenterology", 3800, "ALWAYS", "BASIC", 0, [], None),
    # --- surgical
    ("SP-20010", "Appendectomy, laparoscopic", "surgical", "General Surgery", 18500, "ALWAYS", "BASIC", 0, [], None),
    ("SP-20020", "Cholecystectomy, laparoscopic", "surgical", "General Surgery", 24000, "ALWAYS", "BASIC", 0, [], None),
    ("SP-20040", "Knee arthroscopy with partial meniscectomy", "surgical", "Orthopaedics", 21000, "ALWAYS", "BASIC", 0,
     ["Requires documented failure of at least six weeks of conservative treatment"], None),
    ("SP-20050", "Total knee replacement", "surgical", "Orthopaedics", 62000, "ALWAYS", "ENHANCED", 0, [], None),
    ("SP-20060", "Spinal fusion, single level", "surgical", "Neurosurgery", 95000, "ALWAYS", "COMPREHENSIVE", 0, [],
     {"escalation_rule_id": "ESC-002",
      "reason": "The benefit schedule covers spinal fusion for instability, while the exclusions list degenerative "
                "disc disease without instability. Both clauses apply to most submitted cases."}),
    ("SP-20070", "Coronary angioplasty with stent", "surgical", "Cardiology", 78000, "ALWAYS", "ENHANCED", 0, [], None),
    ("SP-20080", "Coronary artery bypass graft", "surgical", "Cardiothoracic Surgery", 145000, "ALWAYS", "ENHANCED", 0, [], None),
    ("SP-20090", "Cataract extraction with intraocular lens", "surgical", "Ophthalmology", 12500, "ALWAYS", "BASIC", 0,
     ["Premium multifocal lenses are an upgrade payable by the member"], None),
    ("SP-20110", "Bariatric surgery, sleeve gastrectomy", "surgical", "Bariatric Surgery", 48000, "ALWAYS", "COMPREHENSIVE", 12, [],
     {"escalation_rule_id": "ESC-001",
      "reason": "Eligibility depends on BMI thresholds, documented supervised weight-management attempts and "
                "comorbidities; submissions rarely evidence all three."}),
    ("SP-20120", "Septoplasty for deviated nasal septum", "surgical", "ENT", 18000, "ALWAYS", "BASIC", 0, [],
     {"escalation_rule_id": "ESC-006",
      "reason": "Functional septoplasty is covered but cosmetic rhinoplasty is excluded; combined procedures need a "
                "clinician to separate the functional component."}),
    ("SP-20130", "Robotic-assisted radical prostatectomy", "surgical", "Urology", 88000, "ALWAYS", "EXECUTIVE", 0, [],
     {"escalation_rule_id": "ESC-005",
      "reason": "The schedule prices open and laparoscopic prostatectomy only; the robotic approach has no tariff "
                "line and no stated cover position."}),
    ("SP-20140", "Rhinoplasty, cosmetic", "surgical", "Plastic Surgery", 32000, "NEVER", "NONE", 0,
     ["Cosmetic surgery is excluded on all tiers unless it reconstructs an accidental injury covered by the policy"], None),
    ("SP-20150", "Hair transplantation", "surgical", "Dermatology", 25000, "NEVER", "NONE", 0,
     ["Cosmetic treatment excluded on all tiers"], None),
    # --- maternity (12-month waiting period on every tier)
    ("SP-30010", "Antenatal care package", "maternity", "Obstetrics", 4500, "ABOVE_TIER_THRESHOLD", "BASIC", 12, [], None),
    ("SP-30020", "Normal vaginal delivery", "maternity", "Obstetrics", 12000, "ALWAYS", "BASIC", 12, [], None),
    ("SP-30030", "Caesarean section, elective", "maternity", "Obstetrics", 22000, "ALWAYS", "BASIC", 12, [], None),
    ("SP-30040", "Caesarean section, emergency", "maternity", "Obstetrics", 26000, "ALWAYS", "BASIC", 12, [], None),
    ("SP-30050", "Neonatal intensive care, per day", "maternity", "Neonatology", 3500, "ALWAYS", "BASIC", 0, [], None),
    ("SP-30060", "IVF treatment cycle", "maternity", "Reproductive Medicine", 38000, "ALWAYS", "EXECUTIVE", 24, [],
     {"escalation_rule_id": "ESC-002",
      "reason": "Assisted reproduction appears as an Executive benefit and in the general exclusions list; cycle "
                "limits and age criteria are not stated."}),
    # --- dental
    ("SP-40010", "Dental consultation and examination", "dental", "Dentistry", 200, "NEVER", "ENHANCED", 6, [], None),
    ("SP-40040", "Root canal treatment, molar", "dental", "Dentistry", 1800, "ABOVE_TIER_THRESHOLD", "COMPREHENSIVE", 3, [], None),
    ("SP-40050", "Dental crown, porcelain", "dental", "Dentistry", 3200, "ALWAYS", "COMPREHENSIVE", 3, [], None),
    ("SP-40060", "Dental implant, single tooth", "dental", "Dentistry", 9500, "ALWAYS", "EXECUTIVE", 0, [],
     {"escalation_rule_id": "ESC-006",
      "reason": "Implants are covered when they replace teeth lost in a covered accident and excluded when elective; "
                "the distinction depends on records the caller may not hold."}),
    # --- optical
    ("SP-50010", "Optometry eye examination", "optical", "Optometry", 250, "NEVER", "ENHANCED", 6,
     ["One examination per policy year"], None),
    ("SP-50020", "Prescription spectacle lenses", "optical", "Optometry", 700, "ABOVE_TIER_THRESHOLD", "ENHANCED", 6,
     ["One pair every 24 months; frames subject to the optical sub-limit"], None),
    ("SP-50030", "LASIK refractive surgery", "optical", "Ophthalmology", 14000, "NEVER", "NONE", 0,
     ["Refractive surgery for cosmetic or convenience reasons is excluded on all tiers"], None),
    # --- chronic
    ("SP-60010", "Diabetes management programme, annual", "chronic", "Endocrinology", 2400, "ABOVE_TIER_THRESHOLD", "BASIC", 6, [], None),
    ("SP-60020", "Insulin pump consumables, six months", "chronic", "Endocrinology", 8500, "ALWAYS", "COMPREHENSIVE", 3, [], None),
    ("SP-60030", "Haemodialysis, per session", "chronic", "Nephrology", 900, "ALWAYS", "BASIC", 6, [], None),
    ("SP-60040", "Chemotherapy cycle, standard regimen", "chronic", "Oncology", 14500, "ALWAYS", "BASIC", 6, [], None),
    ("SP-60050", "Biologic therapy for rheumatoid arthritis, monthly", "chronic", "Rheumatology", 9800, "ALWAYS", "COMPREHENSIVE", 3, [],
     {"escalation_rule_id": "ESC-001",
      "reason": "Cover requires evidence that two conventional therapies failed; submissions usually omit the "
                "treatment history needed to confirm it."}),
    ("SP-60060", "Proton beam radiotherapy", "chronic", "Oncology", 210000, "ALWAYS", "EXECUTIVE", 0, [],
     {"escalation_rule_id": "ESC-005",
      "reason": "Not listed in the benefit schedule and treated as emerging technology; each request needs medical "
                "director review against published evidence."}),
    # --- mental health
    ("SP-70010", "Psychiatric consultation, outpatient", "mental_health", "Psychiatry", 600, "ABOVE_TIER_THRESHOLD", "ENHANCED", 0, [], None),
    ("SP-70020", "Psychotherapy session, 45 minutes", "mental_health", "Psychology", 450, "NEVER", "ENHANCED", 0,
     ["Limited to twelve sessions per policy year"], None),
    ("SP-70030", "Inpatient psychiatric admission, per day", "mental_health", "Psychiatry", 2200, "ALWAYS", "COMPREHENSIVE", 0, [],
     {"escalation_rule_id": "ESC-002",
      "reason": "The mental-health sub-limit and the inpatient benefit give different day limits for the same "
                "admission."}),
    # --- preventive and emergency
    ("SP-80010", "Annual health screening package", "preventive", "Preventive Medicine", 1800, "ABOVE_TIER_THRESHOLD", "COMPREHENSIVE", 0, [], None),
    ("SP-80020", "Influenza vaccination", "preventive", "Preventive Medicine", 120, "NEVER", "BASIC", 0, [], None),
    ("SP-80030", "Emergency department attendance", "emergency", "Emergency Medicine", 1500, "NEVER", "BASIC", 0,
     ["Emergency care never requires pre-authorisation; notify the insurer within 24 hours of admission"], None),
    ("SP-80050", "Air ambulance evacuation", "emergency", "Emergency Medicine", 85000, "ALWAYS", "EXECUTIVE", 0,
     ["Requires insurer coordination except where life-threatening and clinically unavoidable"], None),
]

# --------------------------------------------------------------------------- providers
# (provider_id, name, emirate, area, facility_type, lowest_network, specialties, status)

PROVIDERS = [
    ("PRV-30011", "Al Hudaiba Crescent Hospital", "Dubai", "Al Hudaiba", "hospital", "BASIC_NETWORK",
     ["General Surgery", "Orthopaedics", "Cardiology", "Radiology", "Laboratory", "Emergency Medicine",
      "Obstetrics", "Neonatology", "Preventive Medicine"], "active"),
    ("PRV-30012", "Jumeirah Dunes Specialist Centre", "Dubai", "Jumeirah 2", "clinic", "ENHANCED_NETWORK",
     ["Dermatology", "ENT", "Ophthalmology", "Radiology", "Preventive Medicine", "Plastic Surgery"], "active"),
    ("PRV-30013", "Marina Pearl Day Surgery", "Dubai", "Dubai Marina", "day_surgery", "ENHANCED_NETWORK",
     ["General Surgery", "Orthopaedics", "ENT", "Ophthalmology"], "active"),
    ("PRV-30014", "Deira Riverside Polyclinic", "Dubai", "Deira", "clinic", "BASIC_NETWORK",
     ["Laboratory", "Radiology", "Preventive Medicine", "Endocrinology"], "active"),
    ("PRV-30015", "Al Barsha Meadows Medical Centre", "Dubai", "Al Barsha", "clinic", "BASIC_NETWORK",
     ["Laboratory", "Preventive Medicine", "Psychology", "Psychiatry"], "active"),
    ("PRV-30016", "Silicon Oasis Family Clinic", "Dubai", "Dubai Silicon Oasis", "clinic", "BASIC_NETWORK",
     ["Preventive Medicine", "Laboratory", "Optometry"], "pending-onboarding"),
    ("PRV-30017", "Zabeel Heights Cardiac Institute", "Dubai", "Zabeel", "hospital", "COMPREHENSIVE_NETWORK",
     ["Cardiology", "Cardiothoracic Surgery", "Radiology", "Nuclear Medicine", "Emergency Medicine"], "active"),
    ("PRV-30018", "Falcon Bay Oncology Centre", "Dubai", "Dubai Healthcare City", "hospital", "COMPREHENSIVE_NETWORK",
     ["Oncology", "Radiology", "Nuclear Medicine", "Laboratory", "Genetics"], "active"),
    ("PRV-30019", "Palm Grove Dental Studio", "Dubai", "Al Quoz", "clinic", "ENHANCED_NETWORK",
     ["Dentistry"], "active"),
    ("PRV-30020", "Mirdif Vision Optical Centre", "Dubai", "Mirdif", "clinic", "ENHANCED_NETWORK",
     ["Optometry", "Ophthalmology"], "suspended"),
    ("PRV-30021", "Corniche Lagoon Hospital", "Abu Dhabi", "Corniche", "hospital", "BASIC_NETWORK",
     ["General Surgery", "Obstetrics", "Neonatology", "Emergency Medicine", "Radiology", "Laboratory",
      "Gastroenterology"], "active"),
    ("PRV-30022", "Khalifa City Family Medicine Centre", "Abu Dhabi", "Khalifa City A", "clinic", "BASIC_NETWORK",
     ["Preventive Medicine", "Laboratory", "Endocrinology", "Psychology"], "active"),
    ("PRV-30023", "Yas Horizon Specialist Hospital", "Abu Dhabi", "Yas Island", "hospital", "COMPREHENSIVE_NETWORK",
     ["Neurosurgery", "Orthopaedics", "Urology", "Bariatric Surgery", "Radiology", "Emergency Medicine",
      "Rheumatology"], "active"),
    ("PRV-30024", "Al Reem Sands Medical Centre", "Abu Dhabi", "Al Reem Island", "clinic", "ENHANCED_NETWORK",
     ["Pulmonology", "Cardiology", "Laboratory", "Radiology"], "active"),
    ("PRV-30025", "Mussafah Gateway Clinic", "Abu Dhabi", "Mussafah", "clinic", "BASIC_NETWORK",
     ["Preventive Medicine", "Laboratory", "Nephrology"], "active"),
    ("PRV-30026", "Al Majaz Waterfront Hospital", "Sharjah", "Al Majaz", "hospital", "BASIC_NETWORK",
     ["General Surgery", "Obstetrics", "Emergency Medicine", "Radiology", "Laboratory", "Nephrology"], "active"),
    ("PRV-30027", "Al Nahda Crescent Polyclinic", "Sharjah", "Al Nahda", "clinic", "BASIC_NETWORK",
     ["Preventive Medicine", "Dentistry", "Optometry", "Laboratory"], "active"),
    ("PRV-30028", "Sharjah Skyline Fertility Centre", "Sharjah", "Al Khan", "clinic", "EXECUTIVE_NETWORK",
     ["Reproductive Medicine", "Obstetrics", "Laboratory"], "pending-onboarding"),
    ("PRV-30029", "Emirates Ridge Rehabilitation Hospital", "Dubai", "Nad Al Sheba", "hospital", "COMPREHENSIVE_NETWORK",
     ["Psychiatry", "Psychology", "Orthopaedics", "Neurosurgery"], "active"),
    ("PRV-30030", "Gulf Meridian Robotic Surgery Institute", "Abu Dhabi", "Al Maryah Island", "hospital", "EXECUTIVE_NETWORK",
     ["Urology", "General Surgery", "Bariatric Surgery", "Oncology", "Radiology"], "active"),
]

# --------------------------------------------------------------------------- members
# (member_id, policy_number, given, family, nationality, dob, tier, status, emirate, dependents, tenure)
# tenure drives the policy period: "long" (joined last year), "standard" (joined 1 January this year),
# "new" (joined three months ago, so 12-month waiting periods are unserved), "lapsed" (lapsed 31 March this year).

MEMBERS = [
    ("MBR-2026-0001", "POL-SA-2026-100001", "Fatima", "Al Mansoori", "Emirati", "1986-04-17", "EXECUTIVE", "active", "Abu Dhabi",
     [("Saeed", "Al Mansoori", "spouse", "1983-02-09"), ("Hessa", "Al Mansoori", "child", "2016-08-22")], "long"),
    ("MBR-2026-0002", "POL-SA-2026-100002", "Rajesh", "Nair", "Indian", "1979-11-03", "ENHANCED", "active", "Dubai",
     [("Anjali", "Nair", "spouse", "1982-06-14"), ("Arjun", "Nair", "child", "2012-03-05"),
      ("Meera", "Nair", "child", "2018-12-01")], "long"),
    ("MBR-2026-0003", "POL-SA-2026-100003", "Maria Teresa", "Villanueva", "Filipino", "1991-07-29", "BASIC", "active", "Dubai", [], "standard"),
    ("MBR-2026-0004", "POL-SA-2026-100004", "Ahmed", "Fathi", "Egyptian", "1975-01-22", "COMPREHENSIVE", "active", "Sharjah",
     [("Nadia", "Fathi", "spouse", "1980-09-30"), ("Youssef", "Fathi", "child", "2009-04-11")], "long"),
    ("MBR-2026-0005", "POL-SA-2026-100005", "Imran", "Qureshi", "Pakistani", "1988-03-12", "BASIC", "active", "Sharjah", [], "standard"),
    ("MBR-2026-0006", "POL-SA-2026-100006", "Sarah", "Whitfield", "British", "1984-10-08", "EXECUTIVE", "active", "Dubai",
     [("Oliver", "Whitfield", "child", "2019-05-27")], "long"),
    ("MBR-2026-0007", "POL-SA-2026-100007", "Layla", "Haddad", "Lebanese", "1993-02-19", "ENHANCED", "active", "Dubai", [], "new"),
    ("MBR-2026-0008", "POL-SA-2026-100008", "Mohammed", "Rahman", "Bangladeshi", "1990-12-04", "BASIC", "lapsed", "Sharjah", [], "lapsed"),
    ("MBR-2026-0009", "POL-SA-2026-100009", "Kumari", "Perera", "Sri Lankan", "1987-06-25", "BASIC", "active", "Abu Dhabi", [], "standard"),
    ("MBR-2026-0010", "POL-SA-2026-100010", "Bishal", "Thapa", "Nepali", "1994-09-16", "BASIC", "active", "Dubai", [], "new"),
    ("MBR-2026-0011", "POL-SA-2026-100011", "Omar", "Al Balushi", "Emirati", "1981-05-02", "COMPREHENSIVE", "active", "Abu Dhabi",
     [("Shaikha", "Al Balushi", "spouse", "1985-11-19")], "long"),
    ("MBR-2026-0012", "POL-SA-2026-100012", "Priya", "Raghavan", "Indian", "1996-01-30", "ENHANCED", "active", "Dubai", [], "standard"),
    ("MBR-2026-0013", "POL-SA-2026-100013", "Hassan", "Al Sayed", "Syrian", "1972-08-21", "COMPREHENSIVE", "lapsed", "Dubai",
     [("Rana", "Al Sayed", "spouse", "1978-03-07")], "lapsed"),
    ("MBR-2026-0014", "POL-SA-2026-100014", "Grace", "Okonkwo", "Nigerian", "1989-04-05", "ENHANCED", "active", "Dubai", [], "standard"),
    ("MBR-2026-0015", "POL-SA-2026-100015", "Yusuf", "Abdi", "Sudanese", "1983-07-13", "BASIC", "active", "Sharjah",
     [("Amina", "Abdi", "spouse", "1986-10-26"), ("Bilal", "Abdi", "child", "2015-02-14")], "long"),
    ("MBR-2026-0016", "POL-SA-2026-100016", "Elena", "Petrova", "Russian", "1992-11-11", "EXECUTIVE", "active", "Dubai", [], "long"),
    ("MBR-2026-0017", "POL-SA-2026-100017", "Khalid", "Al Otaibi", "Jordanian", "1977-09-09", "ENHANCED", "active", "Abu Dhabi",
     [("Dana", "Al Otaibi", "child", "2011-06-18")], "long"),
    ("MBR-2026-0018", "POL-SA-2026-100018", "Chloe", "Dupont", "French", "1995-03-24", "COMPREHENSIVE", "active", "Dubai", [], "standard"),
    ("MBR-2026-0019", "POL-SA-2026-100019", "Ayesha", "Siddiqui", "Pakistani", "1990-05-15", "BASIC", "active", "Dubai",
     [("Zara", "Siddiqui", "child", "2021-01-09")], "new"),
    ("MBR-2026-0020", "POL-SA-2026-100020", "Daniel", "Mwangi", "Kenyan", "1986-12-28", "ENHANCED", "lapsed", "Abu Dhabi", [], "lapsed"),
]

# --------------------------------------------------------------------------- onboarding

ONBOARDING_REQUIREMENTS = [
    ("ONB-001", "Valid UAE trade licence", "Department of Economy and Tourism (Dubai) or equivalent",
     True, 12, "Must name the facility exactly as it will appear in the network directory."),
    ("ONB-002", "DHA or DOH health facility licence", "Dubai Health Authority / Department of Health - Abu Dhabi",
     True, 12, "Facility licence, not an individual practitioner licence."),
    ("ONB-003", "Medical malpractice insurance certificate", "Licensed UAE insurer",
     True, 12, "Minimum indemnity AED 1,000,000 per claim."),
    ("ONB-004", "Practitioner licence schedule", "DHA / DOH / MOHAP",
     True, 12, "One licence per treating clinician, matched to the declared specialties."),
    ("ONB-005", "Bank IBAN verification letter", "UAE-licensed bank",
     True, 6, "Account name must match the trade licence holder."),
    ("ONB-006", "Signed tariff agreement", "Insurer network department",
     True, 24, "Countersigned schedule of agreed rates in AED."),
    ("ONB-007", "VAT registration certificate", "Federal Tax Authority",
     False, 0, "Required only where annual turnover exceeds the mandatory registration threshold."),
    ("ONB-008", "Facility inspection report", "Insurer network department",
     False, 24, "Required for inpatient and day-surgery facilities."),
]

ONBOARDING_APPLICATIONS = [
    ("ONB-APP-2026-0007", "PRV-30016", "Silicon Oasis Family Clinic", "pending-onboarding",
     {"ONB-001": "submitted", "ONB-002": "submitted", "ONB-003": "expired", "ONB-004": "submitted",
      "ONB-005": "submitted", "ONB-006": "missing", "ONB-007": "submitted", "ONB-008": "not_required"}),
    ("ONB-APP-2026-0011", "PRV-30028", "Sharjah Skyline Fertility Centre", "pending-onboarding",
     {"ONB-001": "submitted", "ONB-002": "submitted", "ONB-003": "submitted", "ONB-004": "missing",
      "ONB-005": "submitted", "ONB-006": "missing", "ONB-007": "missing", "ONB-008": "submitted"}),
    ("ONB-APP-2026-0014", "PRV-30020", "Mirdif Vision Optical Centre", "suspended",
     {"ONB-001": "submitted", "ONB-002": "expired", "ONB-003": "submitted", "ONB-004": "submitted",
      "ONB-005": "submitted", "ONB-006": "submitted", "ONB-007": "submitted", "ONB-008": "not_required"}),
]

ESCALATIONS = [
    ("ESC-001", "Missing documentation",
     "The benefit schedule requires evidence (treatment history, imaging report, clinical notes) that has not been "
     "supplied.",
     "Tell the caller exactly which documents are needed and how to submit them. Escalate if they are unavailable."),
    ("ESC-002", "Conflicting policy clauses",
     "Two clauses of the same policy point to different answers, for example a benefit listed on a tier and also "
     "named in the general exclusions.",
     "Escalate to the medical director's queue. Do not choose a clause."),
    ("ESC-003", "Amount exceeds a limit",
     "The requested amount exceeds the tier's annual limit, the relevant sub-limit, or the member's remaining "
     "balance for the policy year.",
     "Escalate with the amount and the limit; a human decides on partial cover."),
    ("ESC-004", "Disputed diagnosis or procedure coding",
     "The submitted code disagrees with the clinical description, or two codes with different tariffs describe the "
     "same treatment.",
     "Escalate for coding review. Do not re-code the request."),
    ("ESC-005", "Procedure not in the coverage list, or new technology",
     "The procedure has no entry in the benefit schedule, or is an emerging technique with no tariff line.",
     "Escalate to the medical director. Never infer cover from a similar procedure."),
    ("ESC-006", "Clinical versus cosmetic intent",
     "Cover depends on whether the procedure is reconstructive or cosmetic, or on an accident that must be "
     "evidenced.",
     "Escalate for clinical review."),
    ("ESC-007", "Member eligibility in doubt",
     "The policy is lapsed or suspended, the treatment falls inside a waiting period, or a pre-existing condition "
     "clause may apply.",
     "Tell the caller the request cannot proceed on eligibility grounds and escalate."),
    ("ESC-008", "Provider not active in the network",
     "The requesting provider is suspended, still onboarding, or outside the member's network, or the call is a "
     "supplier or onboarding enquiry.",
     "Escalate to the network department. Onboarding questions are never answered by the pre-authorisation desk."),
]
ESCALATION_RULE_IDS = [e[0] for e in ESCALATIONS]

# Documents the benefit schedule requires before a pre-authorisation decision, by procedure.
REQUIRED_DOCUMENTS = {
    "surgical": ["CLINICAL_NOTES", "OPERATIVE_PLAN"],
    "diagnostic": ["CLINICAL_NOTES"],
    "maternity": ["CLINICAL_NOTES"],
    "chronic": ["CLINICAL_NOTES", "PRIOR_TREATMENT_RECORD"],
    "dental": ["CLINICAL_NOTES", "IMAGING_REPORT"],
    "optical": ["CLINICAL_NOTES"],
    "mental_health": ["CLINICAL_NOTES", "REFERRAL_LETTER"],
    "preventive": [],
    "emergency": [],
}
EXTRA_DOCUMENTS = {
    "SP-20040": ["PRIOR_TREATMENT_RECORD"],   # conservative treatment must be evidenced
    "SP-20050": ["IMAGING_REPORT"],
    "SP-20060": ["IMAGING_REPORT"],
    "SP-20110": ["PRIOR_TREATMENT_RECORD"],
    "SP-10110": ["REFERRAL_LETTER"],
    "SP-60050": ["LAB_RESULTS"],
}


# --------------------------------------------------------------------------- builders


def schedule_document_name(tier: dict[str, Any]) -> str:
    return f"{tier['product_name']} Schedule of Benefits 2026"


def procedure_section(code: str) -> str:
    number = [p[0] for p in PROCEDURES].index(code) + 1
    name = next(p[1] for p in PROCEDURES if p[0] == code)
    return f"Section 4.{number} {code}: {name}"


def tier_by_id(tier_id: str) -> dict[str, Any]:
    return next(t for t in TIERS if t["tier_id"] == tier_id)


def networks_for(lowest_network: str) -> list[str]:
    """Networks are nested: a provider in the Basic Network is reachable by every higher tier."""
    order = [t["network"]["network_id"] for t in TIERS]
    return order[order.index(lowest_network):]


def build_policy_tiers() -> dict[str, Any]:
    return {
        "disclaimer": DISCLAIMER,
        "currency": "AED",
        "insurer": "Sawt Assurance (fictional)",
        "effective_from": "2026-01-01",
        "regulatory_context": (
            "The Basic tier mirrors the structure of the DHA-mandated Essential Benefits Plan (minimum cover for "
            "lower-income employees in Dubai). Abu Dhabi equivalents are regulated by DOH. Higher tiers are "
            "commercial products with wider networks and higher limits. All values here are invented."
        ),
        "network_hierarchy": [t["network"]["network_id"] for t in TIERS],
        "network_hierarchy_note": (
            "Networks are nested: any provider in the Basic Network is also reachable on Enhanced, Comprehensive "
            "and Executive. Only Executive covers out-of-network treatment."
        ),
        "tiers": TIERS,
    }


def build_procedures() -> dict[str, Any]:
    entries = []
    for code, name, category, specialty, cost, rule, min_tier, waiting, exclusions, ambiguity in PROCEDURES:
        coverage = {}
        for tier in TIERS:
            tier_id = tier["tier_id"]
            covered = min_tier != "NONE" and TIER_ORDER.index(tier_id) >= TIER_ORDER.index(min_tier)
            if not covered:
                required = False
            elif rule == "ALWAYS":
                required = True
            elif rule == "NEVER":
                required = False
            else:
                required = cost >= tier["pre_authorisation_threshold_aed"]
            sub_limit_key = {"mental_health": "mental_health", "dental": "dental", "optical": "optical",
                             "maternity": "maternity"}.get(category)
            coverage[tier_id] = {
                "covered": covered,
                "pre_authorisation_required": required,
                "network": tier["network"]["network_id"],
                "member_co_payment_percent": (
                    tier["co_payments_percent"]["inpatient"] if category == "surgical"
                    else tier["co_payments_percent"]["diagnostics"] if category == "diagnostic"
                    else tier["co_payments_percent"]["emergency"] if category == "emergency"
                    else tier["co_payments_percent"]["outpatient_consultation"]
                ) if covered else None,
                "applicable_sub_limit_aed": tier["sub_limits_aed"][sub_limit_key] if (covered and sub_limit_key) else None,
                "source_document": schedule_document_name(tier),
                "source_section": procedure_section(code),
                "reason_not_covered": None if covered else (
                    "Excluded on all tiers" if min_tier == "NONE"
                    else f"Benefit starts at the {tier_by_id(min_tier)['name']} tier"
                ),
            }
        entries.append({
            "code": code,
            "code_system": "SYN-CPT-STYLE",
            "name": name,
            "category": category,
            "specialty_required": specialty,
            "typical_billed_amount_aed": cost,
            "pre_authorisation_rule": rule,
            "minimum_tier": min_tier,
            "waiting_period_months": waiting,
            "waiting_period_waived_for_emergency": category in ("emergency", "maternity"),
            "exclusions": exclusions,
            "required_documents": (
                sorted(set(REQUIRED_DOCUMENTS[category] + EXTRA_DOCUMENTS.get(code, [])))
                if any(c["pre_authorisation_required"] for c in coverage.values()) else []
            ),
            "coverage_by_tier": coverage,
            "decision_class": "AMBIGUOUS" if ambiguity else ("EXCLUDED" if min_tier == "NONE" else "CLEAR"),
            "escalation": ambiguity,
        })
    return {
        "disclaimer": DISCLAIMER,
        "currency": "AED",
        "code_system_note": (
            "Codes use a synthetic SP-##### scheme. They are shaped like procedure codes for demonstration but are "
            "not CPT codes and do not map to any real code set."
        ),
        "pre_authorisation_rules": {
            "ALWAYS": "Pre-authorisation is required on every tier that covers the procedure.",
            "ABOVE_TIER_THRESHOLD": "Pre-authorisation is required where the billed amount reaches the tier's "
                                    "pre-authorisation threshold.",
            "NEVER": "No pre-authorisation required; claim submitted directly.",
        },
        "decision_classes": {
            "CLEAR": "The rules decide the case; the assistant prepares a recommendation for human sign-off.",
            "EXCLUDED": "Excluded on every tier; prepare a denial recommendation for human sign-off.",
            "AMBIGUOUS": "Escalate to a human reviewer; see escalation_rules.md.",
        },
        "procedures": entries,
    }


def build_schedule(tier: dict[str, Any], procedures: list[dict[str, Any]]) -> str:
    lines = [
        f"# {schedule_document_name(tier)}",
        "",
        f"> {DISCLAIMER}",
        "",
        f"Tier: {tier['name']} ({tier['tier_id']}). Network: {tier['network']['name']} "
        f"({tier['network']['geography']}). All amounts in AED.",
        "",
        "## Section 1 General",
        "",
        f"1.1 Annual limit: AED {tier['annual_limit_aed']:,}. Pre-authorisation is required at or above "
        f"AED {tier['pre_authorisation_threshold_aed']:,}, and for any procedure the schedule marks as requiring it.",
        "",
        "1.2 Pre-authorisation decisions are made by a qualified clinical reviewer or the medical director. Intake "
        "staff and the automated intake line prepare recommendations; they do not approve or deny requests.",
        "",
        "1.3 Sub-limits: " + ", ".join(f"{k} AED {v:,}" for k, v in tier["sub_limits_aed"].items()) + ".",
        "",
        "1.4 Waiting periods (months): "
        + ", ".join(f"{k} {v}" for k, v in tier["waiting_periods_months"].items()) + ".",
        "",
        "## Section 2 Eligibility",
        "",
        "2.1 The member's policy must be active on the treatment date.",
        "",
        "2.2 The requesting provider must be active in the provider directory and credentialed for the specialty.",
        "",
        f"2.3 {'Out-of-network treatment is covered, reimbursed per the tier notes.' if tier['network']['out_of_network_cover'] else 'Out-of-network providers are not covered on this tier.'}",
        "",
        "## Section 3 Co-payments and documents",
        "",
        "3.1 Member co-payments: "
        + ", ".join(f"{k} {v}%" for k, v in tier["co_payments_percent"].items()) + ".",
        "",
        "3.2 Supporting documents are submitted through the provider portal or the regulator's claims channel "
        "(eClaimLink in Dubai, Shafafiya in Abu Dhabi), quoting the case reference. Documents cannot be accepted "
        "by telephone.",
        "",
        "## Section 4 Procedure schedule",
        "",
    ]
    for entry in procedures:
        cover = entry["coverage_by_tier"][tier["tier_id"]]
        lines.append(f"### {entry['coverage_by_tier'][tier['tier_id']]['source_section']}")
        lines.append("")
        lines.append(f"Category: {entry['category']}. Specialty: {entry['specialty_required']}. "
                     f"Typical billed amount: AED {entry['typical_billed_amount_aed']:,}.")
        if not cover["covered"]:
            lines.append(f"Not covered on this tier. {cover['reason_not_covered']}.")
            if entry["exclusions"]:
                lines.append("Exclusions: " + "; ".join(entry["exclusions"]) + ".")
            lines.append("")
            continue
        lines.append(
            f"Covered. Pre-authorisation required: {'yes' if cover['pre_authorisation_required'] else 'no'}. "
            f"Member co-payment: {cover['member_co_payment_percent']}%."
        )
        if cover["applicable_sub_limit_aed"] is not None:
            lines.append(f"Applicable sub-limit: AED {cover['applicable_sub_limit_aed']:,}.")
        if entry["waiting_period_months"]:
            lines.append(f"Waiting period: {entry['waiting_period_months']} months from policy inception.")
        if entry["required_documents"]:
            lines.append("Required supporting documents: " + ", ".join(entry["required_documents"]) + ".")
        if entry["exclusions"]:
            lines.append("Exclusions: " + "; ".join(entry["exclusions"]) + ".")
        if entry["escalation"]:
            lines.append(
                f"Referred for human review ({entry['escalation']['escalation_rule_id']}): "
                f"{entry['escalation']['reason']}"
            )
        lines.append("")
    return "\n".join(lines)


def build_providers() -> dict[str, Any]:
    entries = []
    for provider_id, name, emirate, area, facility_type, lowest_network, specialties, status in PROVIDERS:
        entries.append({
            "provider_id": provider_id,
            "name": name,
            "emirate": emirate,
            "area": area,
            "facility_type": facility_type,
            "regulator": "DHA" if emirate == "Dubai" else "DOH" if emirate == "Abu Dhabi" else "MOHAP",
            "facility_licence_number": f"SYN-{'DHA' if emirate == 'Dubai' else 'DOH' if emirate == 'Abu Dhabi' else 'MOH'}-{provider_id[-5:]}",
            "networks": networks_for(lowest_network),
            "specialties": specialties,
            "status": status,
            "accepts_direct_billing": status == "active",
            "notes": {
                "active": "In network and accepting pre-authorisation requests.",
                "suspended": "Suspended pending resolution of an onboarding document; requests must be escalated.",
                "pending-onboarding": "Onboarding incomplete; not yet eligible to submit pre-authorisation requests.",
            }[status],
        })
    return {"disclaimer": DISCLAIMER, "providers": entries}


def build_onboarding() -> dict[str, Any]:
    checklist = [
        {
            "requirement_id": rid,
            "name": name,
            "issuing_authority": authority,
            "mandatory": mandatory,
            "validity_months": validity,
            "status": "missing",
            "allowed_statuses": ["submitted", "missing", "expired", "not_required"],
            "notes": notes,
        }
        for rid, name, authority, mandatory, validity, notes in ONBOARDING_REQUIREMENTS
    ]
    applications = [
        {
            "application_id": app_id,
            "provider_id": provider_id,
            "provider_name": provider_name,
            "provider_status": status,
            "documents": [{"requirement_id": rid, "status": st} for rid, st in statuses.items()],
            "outstanding": [rid for rid, st in statuses.items() if st in ("missing", "expired")],
        }
        for app_id, provider_id, provider_name, status, statuses in ONBOARDING_APPLICATIONS
    ]
    return {
        "disclaimer": DISCLAIMER,
        "process_note": (
            "Supplier and provider onboarding is handled by the network department, not by the pre-authorisation "
            "desk. Callers asking about onboarding are handed to a human (escalation rule ESC-008)."
        ),
        "checklist": checklist,
        "example_applications": applications,
    }


def policy_period(tenure: str, today: date) -> tuple[date, date | None, date | None]:
    """Start, renewal and lapse dates for a tenure, anchored to the generation year."""
    year = today.year
    if tenure == "long":
        return date(year - 1, 1, 1), date(year, 12, 31), None
    if tenure == "standard":
        return date(year, 1, 1), date(year, 12, 31), None
    if tenure == "new":
        month, start_year = (today.month - 3, year) if today.month > 3 else (today.month + 9, year - 1)
        start = date(start_year, month, 1)
        return start, date(start.year + 1, start.month, 28), None
    if tenure == "lapsed":
        return date(year - 1, 1, 1), None, date(year, 3, 31)
    raise ValueError(f"Unknown tenure {tenure!r}")


def build_members() -> dict[str, Any]:
    today = GENERATED_FOR
    entries = []
    for i, (member_id, policy, given, family, nationality, dob, tier, status, emirate, dependents, tenure) in enumerate(MEMBERS):
        start, renewal, lapse = policy_period(tenure, today)
        entries.append({
            "member_id": member_id,
            "policy_number": policy,
            "given_name": given,
            "family_name": family,
            "nationality": nationality,
            "date_of_birth": dob,
            "emirates_id": f"784-{dob[:4]}-{1000000 + i * 37:07d}-{i % 10}",
            "mobile": f"+9715{(60000000 + i * 111111) % 100000000:08d}",
            "tier": tier,
            "policy_status": status,
            "policy_tenure": tenure,
            "policy_start_date": start.isoformat(),
            "policy_renewal_date": renewal.isoformat() if renewal else None,
            "policy_lapse_date": lapse.isoformat() if lapse else None,
            "emirate_of_residence": emirate,
            "sponsor": "Fictional Employer LLC",
            "dependents": [
                {"given_name": g, "family_name": f, "relationship": rel, "date_of_birth": d,
                 "member_id": f"{member_id}-D{n + 1}"}
                for n, (g, f, rel, d) in enumerate(dependents)
            ],
            "maternity_waiting_period_satisfied": (
                status == "active" and ((today.year - start.year) * 12 + today.month - start.month) >= 12
            ),
        })
    return {
        "disclaimer": DISCLAIMER,
        "verification_note": (
            "Member verification during a call must use the member lookup tool, not retrieval from this file. These "
            "records exist so demo calls have consistent people to talk about."
        ),
        "members": entries,
    }


ESCALATION_TABLE = "\n".join(
    f"| **{rid}** | **{title}.** {situation} | {action} |" for rid, title, situation, action in ESCALATIONS
)

ESCALATION_MARKDOWN = f"""# Escalation rules

> {DISCLAIMER}

A pre-authorisation request is **rule-based** when the benefit schedule decides it: the member is verified and
active, the procedure appears in `procedure_coverage.json`, the tier covers it, the documentation required by the
schedule is present, and the amount sits inside the tier's limits. The assistant prepares a recommendation and a
qualified human signs it off.

A request is **not rule-based** when any of the situations below applies. The assistant must then stop preparing a
recommendation and hand the case to a human. It must never approve, deny, or predict the outcome.

| Rule | Situation | What the assistant does |
|---|---|---|
{ESCALATION_TABLE}

## How this maps to the assistant's tools

- The coverage check returns one of three outcomes: a clean recommendation, a request for more information, or
  **escalate**.
- Every ambiguous procedure in `procedure_coverage.json` carries an `escalation.escalation_rule_id` pointing at one
  of the rules above, so the reason given to the reviewer is the same one written here.
- Escalation is a routing decision, not a clinical one. The assistant records the reason and hands over; a
  clinical reviewer or the medical director decides.

## Worked examples

1. **Bariatric sleeve gastrectomy (SP-20110), Comprehensive tier.** Covered on paper, but eligibility rests on BMI,
   supervised weight-management history and comorbidities. Missing evidence of the second and third: **ESC-003 plus
   ESC-001** — escalate.
2. **Robotic-assisted prostatectomy (SP-20130), Enhanced tier.** The tier does not reach this benefit and the
   schedule has no robotic tariff line: **ESC-005** — escalate, do not deny on the tier alone.
3. **Septoplasty (SP-20120) submitted with a rhinoplasty code.** Functional versus cosmetic intent is unclear and
   the coding is disputed: **ESC-006 plus ESC-004** — escalate.
4. **Normal delivery (SP-30020) for a member eight months into the policy.** The 12-month maternity waiting period
   is unsatisfied: **ESC-007** — escalate rather than deny in the call.
5. **MRI knee (SP-10050), Basic tier, AED 2,400.** Covered, above the AED 1,000 threshold, conservative-treatment
   evidence attached: rule-based, so prepare a recommendation for human sign-off.
"""


def build_readme() -> str:
    tier_rows = "\n".join(
        f"| {t['name']} | {t['annual_limit_aed']:,} | {t['network']['name']} | "
        f"{t['pre_authorisation_threshold_aed']:,} | {t['co_payments_percent']['outpatient_consultation']}% |"
        for t in TIERS
    )
    counts = {"CLEAR": 0, "EXCLUDED": 0, "AMBIGUOUS": 0}
    for entry in build_procedures()["procedures"]:
        counts[entry["decision_class"]] += 1
    return f"""# Synthetic UAE pre-authorisation knowledge base

> {DISCLAIMER}

Demo and test data for a pre-authorisation voice agent: policy tiers, procedure coverage, network providers,
provider onboarding requirements, sample members, and the rules that decide when a case must go to a human.

## Files

| File | Contents | Keys that link it to the others |
|---|---|---|
| `policy_tiers.json` | 4 tiers with annual and sub-limits, networks, co-payments, pre-authorisation thresholds and waiting periods | `tier_id`, `network.network_id` |
| `procedure_coverage.json` | {len(PROCEDURES)} procedures with per-tier cover, pre-authorisation flags, exclusions, waiting periods and escalation reasons | `coverage_by_tier.<tier_id>`, `specialty_required`, `escalation.escalation_rule_id` |
| `network_providers.json` | {len(PROVIDERS)} fictional facilities across Dubai, Abu Dhabi and Sharjah | `networks[]`, `specialties[]`, `status` |
| `supplier_onboarding_requirements.json` | Onboarding checklist and three in-flight applications | `requirement_id`, `provider_id` |
| `sample_members.json` | {len(MEMBERS)} members with dependants, tiers and policy status | `tier`, `policy_number`, `member_id` |
| `escalation_rules.md` | Plain-language description of what is *not* rule-based | `ESC-001` … `ESC-008` |
| `escalation_rules.json` | The same rules, machine-readable; the rules engine cites this text verbatim | `rule_id` |
| `schedule-<tier>.md` | Per-tier schedule of benefits; the document coverage decisions cite | `Section 4.n <code>` |

## How they fit together

```
sample_members.json ──tier──> policy_tiers.json <──network──> network_providers.json
                                     │                                  │
                          coverage_by_tier / thresholds        specialty_required
                                     ▼                                  │
                         procedure_coverage.json <───────────────────────┘
                                     │
                     escalation.escalation_rule_id
                                     ▼
                           escalation_rules.md
```

A coverage lookup runs: verify the member and read their tier → find the procedure → read `coverage_by_tier` for
that tier → check the amount against the tier threshold and sub-limit → check the provider is active in that tier's
network → if the procedure is `AMBIGUOUS`, or any escalation rule fires, hand to a human.

## Tiers at a glance

| Tier | Annual limit (AED) | Network | Pre-auth threshold (AED) | Outpatient co-pay |
|---|---|---|---|---|
{tier_rows}

Networks are nested: Basic ⊂ Enhanced ⊂ Comprehensive ⊂ Executive. Only Executive covers out-of-network care.

## Deliberate test cases

Of {len(PROCEDURES)} procedures: **{counts['CLEAR']} clear**, **{counts['EXCLUDED']} excluded on every tier**, and
**{counts['AMBIGUOUS']} ambiguous**. The ambiguous ones exist to trigger escalation rather than a clean
approve/deny, and each names the escalation rule it should raise.

Useful demo calls:
- Clear approval path: MRI knee `SP-10050` for an active Enhanced member at an active provider.
- Missing documentation: biologic therapy `SP-60050` (ESC-001).
- Excluded: cosmetic rhinoplasty `SP-20140`.
- Tier boundary: total knee replacement `SP-20050` for a Basic member (benefit starts at Enhanced).
- Eligibility: any request for a lapsed member (`MBR-2026-0008`, `MBR-2026-0013`, `MBR-2026-0020`) — ESC-007.
- Provider problem: a request from `PRV-30020` (suspended) or `PRV-30016` (onboarding) — ESC-008.

## Using the catalogue with AssemblyAI

The catalogue stays in this application; it is not uploaded to the voice provider. Load it into the application
database, then provision the AssemblyAI agents:

```bash
uv run python -m preauth.seed
uv run python scripts/assemblyai_setup.py
```

`assemblyai_setup.py` derives recognition keyterms from the catalogue, but coverage facts, rules and citations
reach the agent only through the application's three constrained tools. This keeps the database/rules engine as
the authoritative source and prevents provider-side document retrieval from bypassing application guardrails.

## Consistency and regeneration

These files are generated and validated by `scripts/generate_uae_knowledge_base.py`; edit that script, not the
JSON. Validation enforces: per-tier pre-authorisation flags match each procedure's rule and the tier threshold;
excluded procedures never require pre-authorisation; every referenced tier, network and escalation rule exists;
networks nest correctly; every clearly-decidable procedure's specialty is offered by an active provider; amounts
sit within tier limits; and member tiers, policy numbers and dependant records are valid and unique.

```bash
uv run python scripts/generate_uae_knowledge_base.py --check
```

## How the backend uses these files

`preauth.seed` loads this catalogue into the database, and the rules engine evaluates those tables. Every citation
the agent reads out comes from a tool result and resolves to a section that exists here: the per-tier schedules
carry the `Section 4.n` headings the coverage rules cite, and `escalation_rules.md` carries the ESC-### text an
escalation quotes.

There is no second source of coverage data anywhere in the repository. Editing this catalogue (through the
generator) changes what the agent decides.
"""


# --------------------------------------------------------------------------- validation


def validate(files: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    tiers = {t["tier_id"]: t for t in files["policy_tiers.json"]["tiers"]}
    networks = {t["network"]["network_id"] for t in tiers.values()}
    procedures = files["procedure_coverage.json"]["procedures"]
    providers = files["network_providers.json"]["providers"]
    members = files["sample_members.json"]["members"]
    onboarding = files["supplier_onboarding_requirements.json"]

    if list(tiers) != TIER_ORDER:
        errors.append(f"tier ids/order unexpected: {list(tiers)}")

    active_specialties = {s for p in providers if p["status"] == "active" for s in p["specialties"]}
    listed_specialties = {s for p in providers for s in p["specialties"]}
    codes = set()
    for proc in procedures:
        code = proc["code"]
        if code in codes:
            errors.append(f"{code}: duplicate procedure code")
        codes.add(code)
        if set(proc["coverage_by_tier"]) != set(tiers):
            errors.append(f"{code}: coverage must list every tier")
        for tier_id, cover in proc["coverage_by_tier"].items():
            tier = tiers[tier_id]
            expected_covered = (
                proc["minimum_tier"] != "NONE"
                and TIER_ORDER.index(tier_id) >= TIER_ORDER.index(proc["minimum_tier"])
            )
            if cover["covered"] != expected_covered:
                errors.append(f"{code}/{tier_id}: covered={cover['covered']} contradicts minimum_tier")
            if not cover["covered"]:
                if cover["pre_authorisation_required"]:
                    errors.append(f"{code}/{tier_id}: not covered but pre-authorisation required")
                if not cover["reason_not_covered"]:
                    errors.append(f"{code}/{tier_id}: not covered without a reason")
                continue
            rule = proc["pre_authorisation_rule"]
            expected = (
                True if rule == "ALWAYS"
                else False if rule == "NEVER"
                else proc["typical_billed_amount_aed"] >= tier["pre_authorisation_threshold_aed"]
            )
            if cover["pre_authorisation_required"] != expected:
                errors.append(
                    f"{code}/{tier_id}: pre-auth flag {cover['pre_authorisation_required']} contradicts rule {rule} "
                    f"at threshold {tier['pre_authorisation_threshold_aed']}"
                )
            if cover["network"] not in networks:
                errors.append(f"{code}/{tier_id}: unknown network {cover['network']}")
            if proc["typical_billed_amount_aed"] > tier["annual_limit_aed"]:
                errors.append(f"{code}/{tier_id}: cost exceeds the tier annual limit")
        if proc["category"] == "maternity" and proc["waiting_period_months"] < 12 and proc["code"] != "SP-30050":
            errors.append(f"{code}: maternity benefits need a 12-month waiting period")
        if proc["decision_class"] == "AMBIGUOUS":
            rule_id = (proc["escalation"] or {}).get("escalation_rule_id")
            if rule_id not in ESCALATION_RULE_IDS:
                errors.append(f"{code}: ambiguous without a valid escalation rule id ({rule_id})")
        elif proc["escalation"]:
            errors.append(f"{code}: non-ambiguous procedure carries an escalation reason")
        if proc["decision_class"] == "EXCLUDED" and not proc["exclusions"]:
            errors.append(f"{code}: excluded everywhere without an exclusion reason")
        # A procedure that decides cleanly must be deliverable today; ambiguous and excluded ones only need to be
        # locatable (e.g. the fertility centre is still onboarding, which is itself an escalation case).
        required_pool = active_specialties if proc["decision_class"] == "CLEAR" else listed_specialties
        needs_preauth = any(c["pre_authorisation_required"] for c in proc["coverage_by_tier"].values())
        if needs_preauth and proc["category"] not in ("preventive", "emergency") and not proc["required_documents"]:
            errors.append(f"{code}: requires pre-authorisation but lists no supporting documents")
        if not needs_preauth and proc["required_documents"]:
            errors.append(f"{code}: lists documents but never requires pre-authorisation")
        if proc["specialty_required"] not in required_pool:
            errors.append(
                f"{code}: no {'active ' if proc['decision_class'] == 'CLEAR' else ''}provider offers "
                f"{proc['specialty_required']}"
            )

    provider_ids = set()
    for provider in providers:
        if provider["provider_id"] in provider_ids:
            errors.append(f"{provider['provider_id']}: duplicate provider id")
        provider_ids.add(provider["provider_id"])
        if not set(provider["networks"]) <= networks:
            errors.append(f"{provider['provider_id']}: unknown network in {provider['networks']}")
        expected = networks_for(provider["networks"][0])
        if provider["networks"] != expected:
            errors.append(f"{provider['provider_id']}: networks must nest upwards, got {provider['networks']}")
        if provider["status"] not in ("active", "suspended", "pending-onboarding"):
            errors.append(f"{provider['provider_id']}: invalid status {provider['status']}")
        if not provider["specialties"]:
            errors.append(f"{provider['provider_id']}: no specialties")

    seen_policies, seen_members = set(), set()
    for member in members:
        if member["tier"] not in tiers:
            errors.append(f"{member['member_id']}: unknown tier {member['tier']}")
        if member["policy_number"] in seen_policies:
            errors.append(f"{member['member_id']}: duplicate policy number")
        seen_policies.add(member["policy_number"])
        if member["member_id"] in seen_members:
            errors.append(f"{member['member_id']}: duplicate member id")
        seen_members.add(member["member_id"])
        if member["policy_status"] not in ("active", "lapsed"):
            errors.append(f"{member['member_id']}: invalid policy status")
        if member["policy_status"] == "active" and member["policy_renewal_date"] is None:
            errors.append(f"{member['member_id']}: active policy without a renewal date")
        if (member["policy_status"] == "active") != (member["policy_lapse_date"] is None):
            errors.append(f"{member['member_id']}: lapse date inconsistent with policy status")
        for dependent in member["dependents"]:
            if not dependent["member_id"].startswith(member["member_id"]):
                errors.append(f"{member['member_id']}: dependent id not derived from the member id")

    requirement_ids = {r["requirement_id"] for r in onboarding["checklist"]}
    allowed = {"submitted", "missing", "expired", "not_required"}
    for application in onboarding["example_applications"]:
        if application["provider_id"] not in provider_ids:
            errors.append(f"{application['application_id']}: unknown provider {application['provider_id']}")
        submitted_ids = {d["requirement_id"] for d in application["documents"]}
        if submitted_ids != requirement_ids:
            errors.append(f"{application['application_id']}: documents must cover every checklist requirement")
        for document in application["documents"]:
            if document["status"] not in allowed:
                errors.append(f"{application['application_id']}: invalid status {document['status']}")
        outstanding = {d["requirement_id"] for d in application["documents"] if d["status"] in ("missing", "expired")}
        if set(application["outstanding"]) != outstanding:
            errors.append(f"{application['application_id']}: outstanding list does not match document statuses")
    return errors


def render() -> dict[str, Any]:
    procedures = build_procedures()
    schedules = {
        f"schedule-{t['tier_id'].lower()}.md": build_schedule(t, procedures["procedures"]) for t in TIERS
    }
    return {
        "policy_tiers.json": build_policy_tiers(),
        "procedure_coverage.json": procedures,
        **schedules,
        "network_providers.json": build_providers(),
        "supplier_onboarding_requirements.json": build_onboarding(),
        "sample_members.json": build_members(),
        "escalation_rules.json": {
            "disclaimer": DISCLAIMER,
            "note": "Machine-readable form of escalation_rules.md; the rules engine cites this text verbatim.",
            "rules": [
                {"rule_id": rid, "title": title, "situation": situation, "agent_action": action}
                for rid, title, situation, action in ESCALATIONS
            ],
        },
        "escalation_rules.md": ESCALATION_MARKDOWN,
        "README.md": build_readme(),
    }


def serialise(name: str, content: Any) -> str:
    return content if name.endswith(".md") else json.dumps(content, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    files = render()
    errors = validate(files)
    if errors:
        print(f"{len(errors)} consistency error(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    if "--check" in sys.argv:
        stale = [
            n for n, c in files.items()
            if not (OUT / n).exists() or (OUT / n).read_text(encoding="utf-8") != serialise(n, c)
        ]
        if stale:
            print(f"Stale knowledge base files: {stale}. Run scripts/generate_uae_knowledge_base.py", file=sys.stderr)
            return 1
        print(f"knowledge_base/ is current and consistent ({len(files)} files).")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (OUT / name).write_text(serialise(name, content), encoding="utf-8")
        print(f"wrote knowledge_base/{name}")
    print(f"\nValidated: {len(PROCEDURES)} procedures, {len(PROVIDERS)} providers, {len(MEMBERS)} members, "
          f"{len(TIERS)} tiers — no consistency errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
