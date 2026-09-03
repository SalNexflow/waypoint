"""The domain: aircon servicing skills, parts, and job archetypes.

Everything the constraint model will reason about is declared here, so the
vocabulary is fixed in one place. Phase 4 will consume these same strings.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Skills -----------------------------------------------------------------
# Ordered roughly by how common they are. `chiller` and `vrv_vrf` are the
# specialist ones -- few technicians hold them, which is exactly what makes
# skill matching a binding constraint rather than decoration.

SKILLS: tuple[str, ...] = (
    "split_unit",
    "electrical",
    "refrigerant_handling",
    "ducted",
    "vrv_vrf",
    "chiller",
)

# How likely a technician is to hold each skill, before coverage is enforced.
SKILL_PREVALENCE: dict[str, float] = {
    "split_unit": 0.95,
    "electrical": 0.65,
    "refrigerant_handling": 0.55,
    "ducted": 0.40,
    "vrv_vrf": 0.25,
    "chiller": 0.20,
}

# --- Parts ------------------------------------------------------------------

PARTS: tuple[str, ...] = (
    "filter_set",
    "gas_r32",
    "gas_r410a",
    "contactor",
    "thermostat",
    "drain_pump",
    "pcb_board",
    "compressor",
)

# Bulky or expensive parts are not carried speculatively. A van has filters
# and gas; it does not have a spare compressor unless the job called for one.
PART_CARRY_RATE: dict[str, float] = {
    "filter_set": 0.95,
    "gas_r32": 0.70,
    "gas_r410a": 0.70,
    "contactor": 0.60,
    "thermostat": 0.50,
    "drain_pump": 0.40,
    "pcb_board": 0.30,
    "compressor": 0.20,
}

# Typical quantity carried when a van does carry the part.
PART_QTY_RANGE: dict[str, tuple[int, int]] = {
    "filter_set": (4, 12),
    "gas_r32": (1, 3),
    "gas_r410a": (1, 3),
    "contactor": (2, 5),
    "thermostat": (1, 3),
    "drain_pump": (1, 2),
    "pcb_board": (1, 2),
    "compressor": (1, 1),
}


@dataclass(frozen=True)
class JobArchetype:
    """A kind of work, with the shape of its duration and requirements.

    `field(default_factory=tuple)` rather than `= ()`:

    Python evaluates default arguments ONCE, at function-definition time, and
    shares that one object across every call. For an immutable default like a
    tuple that is harmless, but for a list or dict it is the classic Python
    footgun -- every instance silently shares one list. Dataclasses refuse to
    let you do it: a mutable default raises ValueError at class creation, and
    default_factory (a zero-argument callable invoked per instance) is the
    supported way. JavaScript has no equivalent trap, because JS default
    parameters are re-evaluated on every call.

    Using default_factory uniformly here means changing a tuple to a list
    later cannot introduce the bug.
    """

    name: str
    weight: float                      # relative frequency in a day's work
    duration_range: tuple[int, int]    # minutes, inclusive
    skills: tuple[str, ...] = field(default_factory=tuple)
    # Parts needed *sometimes* -- (part, probability).
    maybe_parts: tuple[tuple[str, float], ...] = field(default_factory=tuple)
    # Parts needed always.
    parts: tuple[str, ...] = field(default_factory=tuple)
    urgency_bias: float = 0.0          # nudges urgency up (priority NUMBER down)


ARCHETYPES: tuple[JobArchetype, ...] = (
    JobArchetype(
        name="routine_service",
        weight=30.0,
        duration_range=(45, 75),
        skills=("split_unit",),
        parts=("filter_set",),
    ),
    JobArchetype(
        name="deep_clean",
        weight=14.0,
        duration_range=(90, 130),
        skills=("split_unit",),
        parts=("filter_set",),
        maybe_parts=(("drain_pump", 0.20),),
    ),
    JobArchetype(
        name="gas_top_up",
        weight=12.0,
        duration_range=(30, 50),
        skills=("split_unit", "refrigerant_handling"),
        maybe_parts=(("gas_r32", 0.55), ("gas_r410a", 0.45)),
    ),
    JobArchetype(
        name="fault_diagnosis",
        weight=12.0,
        duration_range=(45, 90),
        skills=("electrical",),
        maybe_parts=(("contactor", 0.35), ("thermostat", 0.25)),
        urgency_bias=0.5,
    ),
    JobArchetype(
        name="ducted_service",
        weight=9.0,
        duration_range=(90, 150),
        skills=("ducted",),
        parts=("filter_set",),
    ),
    JobArchetype(
        name="pcb_repair",
        weight=6.0,
        duration_range=(60, 95),
        skills=("electrical",),
        parts=("pcb_board",),
        urgency_bias=0.6,
    ),
    JobArchetype(
        name="compressor_replace",
        weight=5.0,
        duration_range=(150, 215),
        skills=("split_unit", "electrical", "refrigerant_handling"),
        parts=("compressor",),
        maybe_parts=(("gas_r410a", 0.60),),
        urgency_bias=0.8,
    ),
    JobArchetype(
        name="vrv_commissioning",
        weight=6.0,
        duration_range=(120, 180),
        skills=("vrv_vrf", "electrical"),
    ),
    JobArchetype(
        name="chiller_service",
        weight=6.0,
        duration_range=(180, 240),
        skills=("chiller", "refrigerant_handling"),
        urgency_bias=0.4,
    ),
)


# --- Customers --------------------------------------------------------------
# Names are generic composites, deliberately not real businesses.

CUSTOMER_PREFIXES: tuple[str, ...] = (
    "Wisma", "Menara", "Plaza", "Kompleks", "Bangunan", "Sunway", "Setia",
    "Mutiara", "Seri", "Bukit", "Taman", "Pusat",
)

CUSTOMER_SUFFIXES: tuple[str, ...] = (
    "Central", "Utama", "Perdana", "Jaya", "Damai", "Indah", "Sentral",
    "Prima", "Heights", "Business Park", "Medical Centre", "Residency",
    "Tower", "Mall", "Square",
)

# Technician names. Ahmad and Siti are pinned in deliberately: the spec's
# natural-language examples in phase 13 are "Ahmad called in sick" and "Siti
# has to leave at 4 today", and those should work against seeded data.
TECHNICIAN_NAMES: tuple[str, ...] = (
    "Ahmad Faizal",
    "Siti Nurhaliza",
    "Lim Wei Jian",
    "Ravi Kumar",
    "Nurul Ain",
    "Tan Chee Meng",
    "Muhammad Haziq",
    "Priya Devi",
    "Wong Kar Wai",
    "Zulkifli Hassan",
    "Lee Mei Ling",
    "Arjun Nair",
    "Farah Adilah",
    "Chong Yew Seng",
    "Iskandar Rahman",
    "Kavitha Selvam",
    "Danial Aiman",
    "Ng Boon Hui",
    "Suria Kamal",
    "Rajesh Menon",
)


# --- Address and job-detail text (field phase 3) -----------------------------
#
# The technician PWA shows a full address, a phone number, what the job is and
# what is wrong with it. None of that existed: the generator produced an
# archetype and a district and threw both away at persist time, and the jobs
# table had a customer name and a point and nothing else.
#
# Generated rather than left blank because a screen full of em dashes tells
# you nothing about whether the screen works. The addresses are synthetic but
# structurally Malaysian -- "12, Jalan Setiabakti 3, 59100 Bangsar" -- so line
# wrapping, truncation and the geo: handoff get exercised against realistic
# lengths.

STREET_NAMES: tuple[str, ...] = (
    "Setiabakti", "Maarof", "Telawi", "Kiara", "Dutamas", "Semarak",
    "Genting Klang", "Pahang", "Ipoh", "Kuching", "Cheras", "Loke Yew",
    "Klang Lama", "Templer", "Universiti", "Sultan", "Tun Razak", "Ampang",
    "Bukit Bintang", "Imbi", "Raja Chulan", "Damansara", "Kepong", "Puchong",
)

# What the customer said is wrong, per archetype. A technician reads this
# standing at the door, so they are one line, concrete, and in the customer's
# words rather than a diagnosis.
FAULT_DESCRIPTIONS: dict[str, tuple[str, ...]] = {
    "routine_service": (
        "Six-monthly service. No fault reported.",
        "Scheduled service, all four units in the office.",
        "Routine service. Customer notes unit 2 is louder than the others.",
    ),
    "deep_clean": (
        "Visible mould on the vanes. Smells musty when it starts.",
        "Not cleaned in over a year. Airflow noticeably weak.",
        "Deep clean requested after the tenant moved out.",
    ),
    "gas_top_up": (
        "Blowing warm after about twenty minutes.",
        "Cooling poorly since last week. Suspect a slow leak.",
        "Ice forming on the pipe outside.",
    ),
    "fault_diagnosis": (
        "Unit trips the breaker on startup, intermittent since Monday.",
        "Turns on, runs for a minute, shuts off. No error light.",
        "Remote shows E5. Nothing happens when it is switched on.",
    ),
    "ducted_service": (
        "Uneven cooling -- meeting room stays warm, corridor is freezing.",
        "Ducted system service. Customer reports rattling above ceiling.",
        "Airflow dropped across the whole floor.",
    ),
    "pcb_repair": (
        "Display flickers then dies. Suspect the board.",
        "No response from the panel. Power is confirmed at the isolator.",
        "Board replaced last year, same symptom returning.",
    ),
    "compressor_replace": (
        "Compressor seized. Confirmed on the last visit.",
        "Loud grinding then stopped completely. Quoted and approved.",
        "Compressor replacement, parts already ordered.",
    ),
    "vrv_commissioning": (
        "New VRV install, needs commissioning and handover.",
        "Commissioning for levels 3 and 4. Contractor will be on site.",
        "System installed last week, not yet started.",
    ),
    "chiller_service": (
        "Chiller service. Plant room access needed.",
        "Chilled water temperature drifting up through the afternoon.",
        "Annual chiller service. Building engineer will meet you.",
    ),
    "orphan": ("Specialist work. No qualified technician available.",),
}

# Dispatcher or customer notes. Deliberately sparse in the generator -- most
# jobs have none, and the detail screen must look right without one. Every
# line here is something that changes what the technician does on arrival.
JOB_NOTES: tuple[str, ...] = (
    "Building manager wants 20 minutes' notice before you arrive.",
    "Access is through the loading bay at the back, not the main lobby.",
    "Customer is only there before 11am. Call first.",
    "Lift to the roof needs a key from security -- ask at the desk.",
    "Dog on site. Owner says to call from the gate.",
    "Parking is tight. There is a bay reserved for contractors on level B2.",
    "Previous visit was cut short; customer is unhappy. Take the time.",
    "Pay attention to the ceiling tiles -- they are new and the tenant is fussy.",
)

# Human labels for the archetype names, for the "what the job is" line.
SERVICE_TYPE_LABELS: dict[str, str] = {
    "routine_service": "Routine service",
    "deep_clean": "Deep clean",
    "gas_top_up": "Gas top-up",
    "fault_diagnosis": "Fault diagnosis",
    "ducted_service": "Ducted service",
    "pcb_repair": "PCB repair",
    "compressor_replace": "Compressor replacement",
    "vrv_commissioning": "VRV commissioning",
    "chiller_service": "Chiller service",
    "orphan": "Specialist work",
}
