"""
Scores a submission against the H1 labels.

Precision/recall/F1, recall per labelled category (which rules actually earn),
calibration (stated confidence vs observed accuracy), and whether the expected
totals are right on the invoices we caught.
"""

import argparse
import csv
from collections import defaultdict
from pathlib import Path


def load_csv(path):
    with Path(path).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def score(submission_path, labels_path):
    subs = {r["invoice_id"]: r for r in load_csv(submission_path)}
    labels = {r["invoice_id"]: r for r in load_csv(labels_path)}

    tp = fp = fn = tn = 0
    cat_total, cat_caught = defaultdict(int), defaultdict(int)
    buckets = defaultdict(lambda: [0, 0])       # confidence -> [correct, total]
    exact = tp_with_expected = 0
    missed, false_alarms, absent = [], [], []

    for inv_id, lab in labels.items():
        sub = subs.get(inv_id)
        if sub is None:
            # don't score a missing row as a clean prediction -- that inflates
            # TN and hides a short submission
            absent.append(inv_id)
            continue

        truth = int(lab["is_erroneous"])
        pred = int(sub["flagged"])
        cats = [c for c in lab["error_categories"].split("|") if c]

        for c in cats:
            cat_total[c] += 1
            if pred:
                cat_caught[c] += 1

        if truth and pred:
            tp += 1
            if lab["expected_total_cents"]:
                tp_with_expected += 1
                if sub["expected_total_cents"] == lab["expected_total_cents"]:
                    exact += 1
        elif truth:
            fn += 1
            missed.append((inv_id, "|".join(cats)))
        elif pred:
            fp += 1
            false_alarms.append((inv_id, sub["error_category"]))
        else:
            tn += 1

        b = round(float(sub["confidence"]), 2)
        buckets[b][1] += 1
        if pred == truth:
            buckets[b][0] += 1

    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0

    print("=" * 62)
    print(f"INVOICE LEVEL   n={len(labels) - len(absent)}   truly erroneous={tp + fn}")
    print("=" * 62)
    print(f"  TP {tp:4d}   FP {fp:4d}   FN {fn:4d}   TN {tn:4d}")
    print(f"  precision {prec:.3f}   recall {rec:.3f}   F1 {f1:.3f}")

    if absent:
        print(f"\n  WARNING: {len(absent)} labelled invoices missing from the "
              f"submission, not scored: {absent[:5]}")

    print("\nRECALL BY LABELLED CATEGORY")
    print("-" * 62)
    for c in sorted(cat_total, key=lambda k: -cat_total[k]):
        n, got = cat_total[c], cat_caught[c]
        print(f"  {got:3d}/{n:<3d}  {got / n:5.0%}  {c}")

    print("\nCALIBRATION  (stated confidence vs observed accuracy)")
    print("-" * 62)
    for b in sorted(buckets):
        correct, n = buckets[b]
        print(f"  stated {b:.2f}   observed {correct / n:5.1%}   n={n}")

    if tp_with_expected:
        print(f"\nEXPECTED TOTAL exact on true positives: {exact}/{tp_with_expected} "
              f"({exact / tp_with_expected:.0%})")

    if missed:
        print(f"\nMISSED ({len(missed)})")
        print("-" * 62)
        for inv_id, cats in missed[:20]:
            print(f"  {inv_id}  {cats}")

    if false_alarms:
        print(f"\nFALSE ALARMS ({len(false_alarms)})")
        print("-" * 62)
        agg = defaultdict(int)
        for _inv_id, cat in false_alarms:
            agg[cat] += 1
        for cat, n in sorted(agg.items(), key=lambda kv: -kv[1]):
            print(f"  {n:3d}  {cat}")

    return {"precision": prec, "recall": rec, "f1": f1}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--submission", required=True)
    p.add_argument("--labels", required=True)
    a = p.parse_args()
    score(a.submission, a.labels)