"""Static population: customers, accounts, devices, merchants, and the
behavioural structure that background traffic is generated from.

Two things here are load-bearing beyond "make up some rows".

**Social structure.** Each personal account gets a small contact list, an
optional employer, and a few billers. Background P2P traffic is drawn from those
contacts rather than uniformly across the whole account population. Uniform
counterparties would produce a graph with no community structure at all, and
`louvain` finding the planted rings in that graph would prove nothing — the
rings would be the only communities present.

**Organic near-patterns.** A slice of the population deliberately looks like
fraud without being fraud: households sharing a device, housemates settling up in
a loop, accounts going quiet and coming back. These are the false positives.
Planting fraud against a clean background produces a detector that scores 100%
and demonstrates nothing (DESIGN.md §4.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..config import Config
from ..models import Account, AccountType, Customer, Device, Merchant
from ..timeutil import MS_PER_DAY, days

FIRST_NAMES = (
    "Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace", "Heidi", "Ivan",
    "Judy", "Mallory", "Niaj", "Olivia", "Peggy", "Rupert", "Sybil", "Trent",
    "Victor", "Walter", "Yara", "Zane", "Amara", "Bruno", "Chidi", "Dara",
    "Elena", "Farid", "Gita", "Hugo", "Ines", "Jonas", "Kira", "Lars", "Mira",
    "Noor", "Omar", "Petra", "Quinn", "Rosa", "Soren", "Tariq", "Ursula",
)
LAST_NAMES = (
    "Adeyemi", "Bianchi", "Costa", "Dubois", "Eriksen", "Fischer", "Garcia",
    "Haddad", "Ibrahim", "Jensen", "Kowalski", "Lindqvist", "Moreau", "Novak",
    "Okafor", "Petrov", "Quintero", "Rossi", "Silva", "Tanaka", "Ustinov",
    "Vargas", "Weber", "Xu", "Yilmaz", "Zhang", "Andersson", "Brennan",
)
COUNTRIES = ("US", "GB", "DE", "FR", "NL", "ES", "IT", "SE", "PL", "IE")
COUNTRY_WEIGHTS = (0.32, 0.16, 0.12, 0.09, 0.07, 0.06, 0.06, 0.05, 0.04, 0.03)

OPERATING_SYSTEMS = ("iOS 18", "iOS 19", "Android 15", "Android 16",
                     "Windows 11", "macOS 15", "Linux")
OS_WEIGHTS = (0.18, 0.22, 0.20, 0.19, 0.11, 0.08, 0.02)

MERCHANT_WORDS = (
    "Northwind", "Blue Harbour", "Cedar", "Vantage", "Orchard", "Kestrel",
    "Lumen", "Granite", "Willow", "Ember", "Foxglove", "Meridian", "Harbour",
    "Tidewater", "Ironbridge", "Larkspur", "Quarry", "Sable", "Thistle",
)
MERCHANT_SUFFIXES = ("Market", "Coffee", "Pharmacy", "Grocers", "Fuel",
                     "Books", "Electronics", "Transit", "Utilities", "Clinic")
MCC_CODES = ("5411", "5812", "5912", "5541", "5942", "5732", "4111", "4900",
             "8011", "5651")


@dataclass(slots=True)
class Population:
    """The generated population plus the behavioural indexes traffic needs."""

    customers: list[Customer]
    accounts: list[Account]
    devices: list[Device]
    merchants: list[Merchant]

    # index -> behavioural structure (parallel to ``accounts``)
    personal_idx: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.int64))
    business_idx: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.int64))
    merchant_account: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.int64))
    account_devices: list[list[int]] = field(default_factory=list)
    device_weights: list[np.ndarray] = field(default_factory=list)
    contacts: list[np.ndarray] = field(default_factory=list)
    employer: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.int64))
    payday: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.int64))
    pay_period: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.int64))
    billers: list[np.ndarray] = field(default_factory=list)
    bill_day: list[np.ndarray] = field(default_factory=list)
    bill_period: list[np.ndarray] = field(default_factory=list)

    # organic near-patterns, kept so the report can separate "false positive"
    # from "false positive we deliberately planted"
    family_groups: list[list[int]] = field(default_factory=list)
    settle_up_groups: list[list[int]] = field(default_factory=list)
    seasonal_dormant: list[int] = field(default_factory=list)

    # Padded lookup tables built by ``finalize()``. Ragged per-account lists are
    # fine for construction but useless for generating two million transactions;
    # these let device and counterparty selection be fully vectorized.
    device_matrix: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.int64))
    device_cumw: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.float64))
    contact_matrix: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.int64))
    contact_count: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))

    def finalize(self) -> None:
        """Build the padded device and contact tables. Call after all planting."""
        n = len(self.accounts)
        width = max((len(d) for d in self.account_devices), default=1)
        self.device_matrix = np.zeros((n, width), dtype=np.int64)
        self.device_cumw = np.ones((n, width), dtype=np.float64)
        for i, (devs, weights) in enumerate(zip(self.account_devices, self.device_weights)):
            self.device_matrix[i, :len(devs)] = devs
            self.device_matrix[i, len(devs):] = devs[-1]
            cw = np.cumsum(weights / weights.sum())
            self.device_cumw[i, :len(cw)] = cw
            self.device_cumw[i, len(cw):] = 1.0

        cwidth = max((len(c) for c in self.contacts), default=1) or 1
        self.contact_matrix = np.zeros((n, cwidth), dtype=np.int64)
        self.contact_count = np.zeros(n, dtype=np.int64)
        for i, contacts in enumerate(self.contacts):
            if len(contacts):
                self.contact_matrix[i, :len(contacts)] = contacts
                self.contact_count[i] = len(contacts)

    def pick_devices(self, src: np.ndarray, rng: np.random.Generator) -> np.ndarray:
        """Vectorized weighted device choice for a batch of source accounts."""
        draws = rng.random(len(src))[:, None]
        col = (draws > self.device_cumw[src]).sum(axis=1)
        col = np.minimum(col, self.device_matrix.shape[1] - 1)
        return self.device_matrix[src, col]

    def account_id(self, idx: int) -> str:
        return self.accounts[idx].account_id

    def device_id(self, idx: int) -> str:
        return self.devices[idx].device_id


def build_population(cfg: Config, rng: np.random.Generator) -> Population:
    """Create the full static population for ``cfg``'s profile."""
    p = cfg.profile
    bg = cfg.generation.background
    end_ms = cfg.end_time_ms
    span_ms = days(p.span_days)
    start_ms = end_ms - span_ms

    # ---- customers -----------------------------------------------------
    # Onboarding predates the observation window by up to five years, so
    # "account age" is a real attribute rather than an artefact of the span.
    onboard_lo = start_ms - days(5 * 365)
    onboard_hi = start_ms - days(1)
    first = rng.integers(0, len(FIRST_NAMES), p.customers)
    last = rng.integers(0, len(LAST_NAMES), p.customers)
    countries = rng.choice(len(COUNTRIES), p.customers, p=np.array(COUNTRY_WEIGHTS))
    kyc = rng.choice([1, 2, 3], p.customers, p=[0.20, 0.55, 0.25])
    onboarded = rng.integers(onboard_lo, onboard_hi, p.customers)

    customers = [
        Customer(
            customer_id=f"CUST-{i:07d}",
            name=f"{FIRST_NAMES[first[i]]} {LAST_NAMES[last[i]]}",
            country=COUNTRIES[countries[i]],
            kyc_level=int(kyc[i]),
            onboarded_at=int(onboarded[i]),
        )
        for i in range(p.customers)
    ]

    # ---- accounts ------------------------------------------------------
    # Customers are assigned round-robin then topped up, so every customer owns
    # at least one account and the surplus spreads rather than concentrating.
    owner = np.concatenate([
        np.arange(p.customers, dtype=np.int64),
        rng.integers(0, p.customers, max(0, p.accounts - p.customers)),
    ])[: p.accounts]

    n_business = max(int(p.accounts * bg.business_account_fraction), p.merchants)
    if n_business > p.accounts:
        raise ValueError(
            f"profile {p.name!r} needs {p.merchants} merchant settlement accounts "
            f"but only has {p.accounts} accounts"
        )
    business_idx = rng.choice(p.accounts, n_business, replace=False)
    business_idx.sort()
    is_business = np.zeros(p.accounts, dtype=bool)
    is_business[business_idx] = True
    personal_idx = np.flatnonzero(~is_business)

    # An account cannot open before its owner onboarded.
    owner_onboard = onboarded[owner]
    opened = owner_onboard + rng.integers(0, days(365), p.accounts)
    opened = np.minimum(opened, end_ms - days(1))

    accounts = [
        Account(
            account_id=f"ACC-{i:07d}",
            customer_id=customers[owner[i]].customer_id,
            opened_at=int(opened[i]),
            account_type=AccountType.BUSINESS if is_business[i] else AccountType.PERSONAL,
        )
        for i in range(p.accounts)
    ]

    # ---- devices -------------------------------------------------------
    dev_os = rng.choice(len(OPERATING_SYSTEMS), p.devices, p=np.array(OS_WEIGHTS))
    dev_seen = rng.integers(start_ms - days(365), end_ms - days(1), p.devices)
    fingerprints = rng.integers(0, 2**48, p.devices)
    devices = [
        Device(
            device_id=f"DEV-{i:07d}",
            fingerprint=f"{int(fingerprints[i]):012x}",
            os=OPERATING_SYSTEMS[dev_os[i]],
            first_seen=int(dev_seen[i]),
        )
        for i in range(p.devices)
    ]

    # ---- merchants -----------------------------------------------------
    m_word = rng.integers(0, len(MERCHANT_WORDS), p.merchants)
    m_suffix = rng.integers(0, len(MERCHANT_SUFFIXES), p.merchants)
    m_mcc = rng.integers(0, len(MCC_CODES), p.merchants)
    m_country = rng.choice(len(COUNTRIES), p.merchants, p=np.array(COUNTRY_WEIGHTS))
    merchants = [
        Merchant(
            merchant_id=f"MER-{i:07d}",
            name=f"{MERCHANT_WORDS[m_word[i]]} {MERCHANT_SUFFIXES[m_suffix[i]]}",
            mcc=MCC_CODES[m_mcc[i]],
            country=COUNTRIES[m_country[i]],
        )
        for i in range(p.merchants)
    ]
    # Each merchant settles into one business account.
    merchant_account = rng.choice(business_idx, p.merchants, replace=False)

    pop = Population(
        customers=customers,
        accounts=accounts,
        devices=devices,
        merchants=merchants,
        personal_idx=personal_idx,
        business_idx=business_idx,
        merchant_account=merchant_account,
    )

    _assign_devices(cfg, rng, pop)
    _assign_social_structure(cfg, rng, pop)
    _plant_organic_near_patterns(cfg, rng, pop)
    pop.finalize()
    return pop


def _assign_devices(cfg: Config, rng: np.random.Generator, pop: Population) -> None:
    """Give every account its own primary device, and some a secondary.

    Devices are dealt out **without replacement**, so no two accounts share
    hardware by accident. Every shared device in the finished dataset is one a
    household or a fraud ring was deliberately given, which is what lets
    "distinct customers on this device" be a signal at all.

    Weighted so the primary dominates: a device carrying most of an account's
    transactions is what makes a *shared* device meaningful.
    """
    n_accounts = len(pop.accounts)
    n_devices = len(pop.devices)
    has_second = rng.random(n_accounts) < 0.35
    needed = n_accounts + int(has_second.sum())
    if needed > n_devices:
        raise ValueError(
            f"profile needs {needed} distinct devices but has {n_devices}; raise "
            f"generation.profiles.<profile>.devices to about 1.4x accounts"
        )
    deal = rng.permutation(n_devices)[:needed]
    primary = deal[:n_accounts]
    secondary = np.full(n_accounts, -1, dtype=np.int64)
    secondary[has_second] = deal[n_accounts:]

    pop.account_devices = []
    pop.device_weights = []
    for i in range(n_accounts):
        if has_second[i] and secondary[i] >= 0:
            pop.account_devices.append([int(primary[i]), int(secondary[i])])
            pop.device_weights.append(np.array([0.82, 0.18]))
        else:
            pop.account_devices.append([int(primary[i])])
            pop.device_weights.append(np.array([1.0]))


def _assign_social_structure(cfg: Config, rng: np.random.Generator, pop: Population) -> None:
    """Contact lists, employers, and billers — the shape of legitimate traffic."""
    bg = cfg.generation.background
    n_accounts = len(pop.accounts)
    personal = pop.personal_idx
    business = pop.business_idx

    lo = bg.contacts_per_account["min"]
    hi = bg.contacts_per_account["max"]
    n_contacts = rng.integers(lo, hi + 1, n_accounts)

    # Contacts are drawn from a local neighbourhood in account order rather than
    # uniformly. Uniform draws give a graph with no community structure, and
    # Louvain finding only the planted rings in such a graph would be a
    # tautology, not a result.
    pop.contacts = []
    for i in range(n_accounts):
        span = min(len(personal), 120)
        centre = int(np.searchsorted(personal, i))
        lo_i = max(0, centre - span // 2)
        hi_i = min(len(personal), lo_i + span)
        pool = personal[lo_i:hi_i]
        pool = pool[pool != i]
        k = int(min(n_contacts[i], len(pool)))
        chosen = rng.choice(pool, k, replace=False) if k else np.array([], dtype=np.int64)
        pop.contacts.append(np.sort(chosen))

    # Employers: a salary credit arrives on a fixed cadence from the same
    # business account. Weekly and biweekly pay are common, and modelling only
    # monthly would cap recurring volume at one event per account per month.
    pop.employer = np.full(n_accounts, -1, dtype=np.int64)
    pop.payday = np.zeros(n_accounts, dtype=np.int64)
    pop.pay_period = np.zeros(n_accounts, dtype=np.int64)
    employed = personal[rng.random(len(personal)) < bg.employed_fraction]
    pop.employer[employed] = rng.choice(business, len(employed))
    pop.payday[employed] = rng.integers(1, 29, len(employed))
    pop.pay_period[employed] = rng.choice(
        bg.salary_periods, len(employed), p=np.asarray(bg.salary_period_weights)
    )

    # Billers: recurring payments to stable business counterparties. A weekly
    # cadence covers subscriptions; monthly covers utilities.
    b_lo = bg.billers_per_account["min"]
    b_hi = bg.billers_per_account["max"]
    pop.billers = [np.array([], dtype=np.int64)] * n_accounts
    pop.bill_day = [np.array([], dtype=np.int64)] * n_accounts
    pop.bill_period = [np.array([], dtype=np.int64)] * n_accounts
    for i in personal:
        k = int(rng.integers(b_lo, b_hi + 1))
        if k:
            pop.billers[i] = rng.choice(business, k, replace=False)
            pop.bill_day[i] = rng.integers(1, 29, k)
            pop.bill_period[i] = rng.choice(
                bg.bill_periods, k, p=np.asarray(bg.bill_period_weights)
            )


def _plant_organic_near_patterns(cfg: Config, rng: np.random.Generator,
                                 pop: Population) -> None:
    """The legitimate lookalikes that generate false positives.

    Households share devices; housemates settle up in a loop; people go quiet
    and come back. None of this is fraud, and a detector that cannot tell it
    apart from the planted rings will say so in its precision score.
    """
    near = cfg.generation.background.organic_near_patterns
    personal = pop.personal_idx

    # -- households sharing a device ------------------------------------
    # Deliberately capped at 2-3 accounts. Fraud rings are 5-8 (§4.3.4), so the
    # two populations sit either side of a threshold the detector has to find,
    # rather than being trivially separable.
    fam_lo, fam_hi = near.family_size["min"], near.family_size["max"]
    n_family_accounts = int(len(personal) * near.shared_device_families)
    pool = rng.permutation(personal)[:n_family_accounts]
    pop.family_groups = []
    cursor = 0
    while cursor < len(pool):
        size = int(rng.integers(fam_lo, fam_hi + 1))
        group = sorted(int(x) for x in pool[cursor:cursor + size])
        cursor += size
        if len(group) < 2:
            break
        shared_device = pop.account_devices[group[0]][0]
        for acct in group[1:]:
            if shared_device not in pop.account_devices[acct]:
                pop.account_devices[acct] = [shared_device] + pop.account_devices[acct]
                weights = np.concatenate([[0.55], pop.device_weights[acct] * 0.45])
                pop.device_weights[acct] = weights / weights.sum()
        pop.family_groups.append(group)

    # -- housemates settling up in a loop -------------------------------
    # These become genuine closed cycles in the transfer graph, and some will be
    # time-respecting. That matters: it means the temporal cycle detector has
    # organic false positives too, not just the static one.
    n_triangles = int(len(personal) * near.settle_up_triangles)
    pop.settle_up_groups = []
    for _ in range(n_triangles):
        group = sorted(int(x) for x in rng.choice(personal, 3, replace=False))
        pop.settle_up_groups.append(group)

    # -- legitimate dormancy --------------------------------------------
    n_dormant = int(len(personal) * near.seasonal_dormancy)
    pop.seasonal_dormant = sorted(
        int(x) for x in rng.choice(personal, n_dormant, replace=False)
    )
