"""Deterministic pre-demo checks; never confirms or creates an action."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]


def run_checks():
    from repositories import Repository
    from scripts.preflight import check_preflight
    import tools
    import transactions

    results = []
    repository = Repository()
    plan = None

    def check(label, operation):
        try:
            passed = bool(operation())
        except Exception:
            passed = False  # Name the failing check without leaking backend details.
        results.append((label, passed))

    before = None

    def preflight():
        nonlocal before
        if check_preflight():
            return False
        before = (repository.list_inventory(), repository.list_purchase_orders())
        return bool(before[0].data and before[1].data and repository.list_knowledge().data)

    check("Data / preflight", preflight)

    def fulfillment():
        nonlocal plan
        plan = tools.plan_fulfillment(po_id="PO-2026-1026")
        return (plan.status == "PARTIALLY_FULFILLABLE" and plan.requested_qty == 20
                and plan.network_available_qty == 4 and plan.fulfilled_qty == 4
                and plan.remaining_qty == 16 and bool(plan.allocations)
                and all(a.evidence for a in plan.allocations) and bool(plan.evidence))

    check("Fulfillment — PO-2026-1026 — 4/20 planned, 16 unresolved", fulfillment)

    def governance():
        result = tools.check_po("PO-2026-1106")
        return (result.status == "BLOCKED" and bool(result.evidence)
                and any(i.code == "DEALER_INELIGIBLE" for i in result.issues))

    check("Governance — PO-2026-1106 blocked", governance)

    def knowledge(query, key):
        result = tools.search_knowledge(query)
        return result.status == "MATCHED" and any(
            m.evidence.source_table == "knowledge" and m.evidence.record_key == key
            for m in result.matches
        )

    check("Knowledge — suspended dealer grounded by KB-009",
          lambda: knowledge("suspended dealer", "KB-009"))
    check("Knowledge — negative stock grounded by KB-007",
          lambda: knowledge("negative available stock", "KB-007"))

    def anomalies():
        result = tools.scan_anomalies()
        return bool(result) and all(i.code and i.evidence for i in result)

    check("Anomaly scanner", anomalies)

    def approval():
        if plan is None or before is None:
            return False
        # Even a regression cannot touch the user's existing action ledger.
        with TemporaryDirectory(prefix="dealerbrain-smoke-") as directory:
            path = Path(directory) / "actions.sqlite"
            result = transactions.execute_simulated_fulfillment(
                "PO-2026-1026", plan, confirmed=False, store_path=path,
            )
            no_write = result.status == "REJECTED" and result.action is None and not path.exists()
        after = (repository.list_inventory(), repository.list_purchase_orders())
        return no_write and before == after

    check("Human approval boundary — zero writes; source inventory/POs unchanged", approval)
    return tuple(results)


def main():
    sys.path.insert(0, str(ROOT))
    print("DealerBRAIN Demo Readiness")
    try:
        results = run_checks()
    except Exception:
        print("FAIL  Local dependencies / initialization")
        print("DEMO READY: FAIL")
        return 1
    for label, passed in results:
        print(f"{'PASS' if passed else 'FAIL'}  {label}")
    passed = sum(ok for _, ok in results)
    print("DEMO READY: " + ("PASS" if passed == len(results) else "FAIL"))
    print(f"{passed}/{len(results)} checks passed")
    return int(passed != len(results))


if __name__ == "__main__":
    raise SystemExit(main())
