"""Mapping of standard financial statement fields to US-GAAP XBRL concepts.

Each field lists the concepts that may carry it, in priority order. For every period the
first concept with a value wins, so a series survives a company switching tags (for
example ``SalesRevenueNet`` to ``RevenueFromContractWithCustomerExcludingAssessedTax``).
The concept actually used is stored in the value's source.

A few fields have an explicit, labelled fallback when no concept is reported:

* ``sum_slots``: the sum of several components (for example current debt = commercial
  paper + current portion of long-term debt). Per slot the first available concept counts.
* ``difference``: one field minus another (for example gross profit = revenue - cost).

Totals such as ``CashCashEquivalentsAndShortTermInvestments`` are deliberately not mapped
to a component field, because that would double count.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from stock_forensics.models import Unit


class Statement(StrEnum):
    INCOME = "income"
    BALANCE = "balance"
    CASH_FLOW = "cash_flow"


class Kind(StrEnum):
    INSTANT = "instant"  # balance sheet item at a point in time
    FLOW = "flow"  # amount over a period; quarters can be derived and summed
    FLOW_NON_ADDITIVE = "flow_non_additive"  # per share / average figures: never derived


UNIT_KEY = {
    Unit.USD: "USD",
    Unit.USD_PER_SHARE: "USD/shares",
    Unit.SHARES: "shares",
}


@dataclass(frozen=True)
class FieldSpec:
    name: str
    label: str
    statement: Statement
    kind: Kind
    unit: Unit
    concepts: tuple[str, ...]
    sum_slots: tuple[tuple[str, ...], ...] = ()
    sum_requires_all: bool = True
    difference: tuple[str, str] | None = None  # (minuend field, subtrahend field)
    concept_notes: dict[str, str] = field(default_factory=dict)

    @property
    def is_flow(self) -> bool:
        return self.kind is not Kind.INSTANT

    @property
    def is_additive(self) -> bool:
        return self.kind is Kind.FLOW

    @property
    def all_concepts(self) -> tuple[str, ...]:
        slot_concepts = tuple(c for slot in self.sum_slots for c in slot)
        return self.concepts + slot_concepts


_INC = Statement.INCOME
_BAL = Statement.BALANCE
_CF = Statement.CASH_FLOW
_FLOW = Kind.FLOW
_NON_ADD = Kind.FLOW_NON_ADDITIVE
_INST = Kind.INSTANT

FIELDS: tuple[FieldSpec, ...] = (
    # --- income statement ---------------------------------------------------------------
    FieldSpec(
        "revenue",
        "Revenue",
        _INC,
        _FLOW,
        Unit.USD,
        (
            "Revenues",
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
        ),
    ),
    FieldSpec(
        "cost_of_revenue",
        "Cost of revenue",
        _INC,
        _FLOW,
        Unit.USD,
        ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices"),
    ),
    FieldSpec(
        "gross_profit",
        "Gross profit",
        _INC,
        _FLOW,
        Unit.USD,
        ("GrossProfit",),
        difference=("revenue", "cost_of_revenue"),
    ),
    FieldSpec(
        "research_and_development",
        "Research and development",
        _INC,
        _FLOW,
        Unit.USD,
        (
            "ResearchAndDevelopmentExpense",
            "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
        ),
    ),
    FieldSpec(
        "selling_general_admin",
        "Selling, general and administrative",
        _INC,
        _FLOW,
        Unit.USD,
        ("SellingGeneralAndAdministrativeExpense",),
        sum_slots=(("SellingAndMarketingExpense",), ("GeneralAndAdministrativeExpense",)),
        sum_requires_all=True,
    ),
    FieldSpec(
        "operating_income",
        "Operating income",
        _INC,
        _FLOW,
        Unit.USD,
        ("OperatingIncomeLoss",),
    ),
    FieldSpec(
        "interest_expense",
        "Interest expense",
        _INC,
        _FLOW,
        Unit.USD,
        (
            "InterestExpense",
            "InterestExpenseNonoperating",
            "InterestExpenseDebt",
            "InterestAndDebtExpense",
        ),
    ),
    FieldSpec(
        "income_before_tax",
        "Income before income taxes",
        _INC,
        _FLOW,
        Unit.USD,
        (
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        ),
    ),
    FieldSpec(
        "income_tax_expense",
        "Income tax expense",
        _INC,
        _FLOW,
        Unit.USD,
        ("IncomeTaxExpenseBenefit",),
    ),
    FieldSpec(
        "net_income",
        "Net income",
        _INC,
        _FLOW,
        Unit.USD,
        ("NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss"),
        concept_notes={"ProfitLoss": "includes noncontrolling interests"},
    ),
    FieldSpec(
        "eps_basic",
        "EPS (basic)",
        _INC,
        _NON_ADD,
        Unit.USD_PER_SHARE,
        ("EarningsPerShareBasic",),
    ),
    FieldSpec(
        "eps_diluted",
        "EPS (diluted)",
        _INC,
        _NON_ADD,
        Unit.USD_PER_SHARE,
        ("EarningsPerShareDiluted",),
    ),
    FieldSpec(
        "shares_basic_weighted",
        "Weighted average shares (basic)",
        _INC,
        _NON_ADD,
        Unit.SHARES,
        ("WeightedAverageNumberOfSharesOutstandingBasic",),
    ),
    FieldSpec(
        "shares_diluted_weighted",
        "Weighted average shares (diluted)",
        _INC,
        _NON_ADD,
        Unit.SHARES,
        ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    ),
    # --- balance sheet ------------------------------------------------------------------
    FieldSpec(
        "cash_and_equivalents",
        "Cash and cash equivalents",
        _BAL,
        _INST,
        Unit.USD,
        (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
            "Cash",
        ),
        concept_notes={
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": (
                "includes restricted cash"
            )
        },
    ),
    FieldSpec(
        "short_term_investments",
        "Short-term investments",
        _BAL,
        _INST,
        Unit.USD,
        (
            "MarketableSecuritiesCurrent",
            "ShortTermInvestments",
            "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        ),
    ),
    FieldSpec(
        "accounts_receivable",
        "Accounts receivable",
        _BAL,
        _INST,
        Unit.USD,
        (
            "AccountsReceivableNetCurrent",
            "ReceivablesNetCurrent",
            "AccountsNotesAndLoansReceivableNetCurrent",
        ),
    ),
    FieldSpec("inventory", "Inventory", _BAL, _INST, Unit.USD, ("InventoryNet",)),
    FieldSpec(
        "total_current_assets",
        "Total current assets",
        _BAL,
        _INST,
        Unit.USD,
        ("AssetsCurrent",),
    ),
    FieldSpec(
        "property_plant_equipment_net",
        "Property, plant and equipment, net",
        _BAL,
        _INST,
        Unit.USD,
        (
            "PropertyPlantAndEquipmentNet",
            "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
        ),
    ),
    FieldSpec("total_assets", "Total assets", _BAL, _INST, Unit.USD, ("Assets",)),
    FieldSpec(
        "accounts_payable",
        "Accounts payable",
        _BAL,
        _INST,
        Unit.USD,
        ("AccountsPayableCurrent", "AccountsPayableTradeCurrent"),
    ),
    FieldSpec(
        "total_current_liabilities",
        "Total current liabilities",
        _BAL,
        _INST,
        Unit.USD,
        ("LiabilitiesCurrent",),
    ),
    FieldSpec(
        "short_term_debt",
        "Short-term debt",
        _BAL,
        _INST,
        Unit.USD,
        ("DebtCurrent",),
        sum_slots=(
            ("ShortTermBorrowings",),
            ("CommercialPaper",),
            ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"),
        ),
        sum_requires_all=False,
    ),
    FieldSpec(
        "long_term_debt",
        "Long-term debt (non-current)",
        _BAL,
        _INST,
        Unit.USD,
        ("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"),
    ),
    FieldSpec(
        "total_liabilities",
        "Total liabilities",
        _BAL,
        _INST,
        Unit.USD,
        ("Liabilities",),
        difference=("total_liabilities_and_equity", "equity_incl_noncontrolling"),
    ),
    FieldSpec(
        "total_liabilities_and_equity",
        "Total liabilities and equity",
        _BAL,
        _INST,
        Unit.USD,
        ("LiabilitiesAndStockholdersEquity",),
    ),
    FieldSpec(
        "retained_earnings",
        "Retained earnings",
        _BAL,
        _INST,
        Unit.USD,
        ("RetainedEarningsAccumulatedDeficit",),
    ),
    FieldSpec(
        "total_equity",
        "Stockholders' equity (parent)",
        _BAL,
        _INST,
        Unit.USD,
        ("StockholdersEquity",),
    ),
    FieldSpec(
        "equity_incl_noncontrolling",
        "Total equity incl. noncontrolling interests",
        _BAL,
        _INST,
        Unit.USD,
        ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",),
    ),
    FieldSpec(
        "shares_outstanding",
        "Shares outstanding",
        _BAL,
        _INST,
        Unit.SHARES,
        ("CommonStockSharesOutstanding",),
    ),
    # --- cash flow statement ------------------------------------------------------------
    FieldSpec(
        "operating_cash_flow",
        "Operating cash flow",
        _CF,
        _FLOW,
        Unit.USD,
        (
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
    ),
    FieldSpec(
        "capital_expenditures",
        "Capital expenditures (payments)",
        _CF,
        _FLOW,
        Unit.USD,
        ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    ),
    FieldSpec(
        "depreciation_amortization",
        "Depreciation and amortization",
        _CF,
        _FLOW,
        Unit.USD,
        (
            "DepreciationDepletionAndAmortization",
            "DepreciationAmortizationAndAccretionNet",
            "DepreciationAndAmortization",
            "Depreciation",
        ),
        concept_notes={
            "Depreciation": "only 'Depreciation' is tagged; may exclude amortization",
            "DepreciationAmortizationAndAccretionNet": "includes accretion",
        },
    ),
    FieldSpec(
        "dividends_paid",
        "Dividends paid",
        _CF,
        _FLOW,
        Unit.USD,
        ("PaymentsOfDividends", "PaymentsOfDividendsCommonStock"),
    ),
    FieldSpec(
        "share_repurchases",
        "Share repurchases",
        _CF,
        _FLOW,
        Unit.USD,
        ("PaymentsForRepurchaseOfCommonStock",),
    ),
    FieldSpec(
        "stock_based_compensation",
        "Stock-based compensation",
        _CF,
        _FLOW,
        Unit.USD,
        ("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"),
    ),
)

FIELD_BY_NAME: dict[str, FieldSpec] = {spec.name: spec for spec in FIELDS}
