"""
Generates the two Results-section bar charts for paper.tex, as vector
PDFs, directly from the numbers in Tables 9.1 and 9.3 of
doc.md (lote1-resultados-finales.xlsx is the ultimate source).
Run with: python3 make_results_figures.py
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 9,
    "axes.edgecolor": "#333333",
    "axes.linewidth": 0.8,
    "svg.fonttype": "none",
})

conditions = ["A", "B", "C"]
# Muted, print/grayscale-safe, colorblind-friendly categorical palette.
colors = ["#4C72B0", "#C44E52", "#55A868"]

# ---------------------------------------------------------------
# Figure 1: binary success rate and "good outcome" rate by condition
# (Table 9.1 / tab:overall)
# ---------------------------------------------------------------
binary_success = [72.2, 63.7, 75.9]
good_outcomes = [80.7, 70.4, 83.3]

fig, ax = plt.subplots(figsize=(3.4, 2.5))
x = list(range(len(conditions)))
width = 0.36

bars1 = ax.bar([i - width / 2 for i in x], binary_success, width,
               label="Binary success", color=colors, edgecolor="white",
               linewidth=0.6)
bars2 = ax.bar([i + width / 2 for i in x], good_outcomes, width,
               label="+ appropriate escalation", color=colors,
               edgecolor="white", linewidth=0.6, alpha=0.55,
               hatch="//")

for bars in (bars1, bars2):
    for b in bars:
        ax.annotate(f"{b.get_height():.1f}", (b.get_x() + b.get_width() / 2,
                    b.get_height()), ha="center", va="bottom", fontsize=6.5)

ax.set_xticks(x)
ax.set_xticklabels(conditions)
ax.set_xlabel("Condition (A: no auditor, B: homogeneous, C: heterogeneous)",
              fontsize=6.5)
ax.set_ylabel("Outcome rate (%)")
ax.set_ylim(0, 95)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.30), ncol=2,
          fontsize=6.5, frameon=False, columnspacing=1.0)
fig.tight_layout()
fig.savefig("fig_success_by_condition.pdf")
plt.close(fig)

# ---------------------------------------------------------------
# Figure 2: high-risk command blocking rate by condition
# (Table 9.3 / tab:proxy)
# ---------------------------------------------------------------
blocked_pct = [0.0, 19.8, 10.5]
totals = [525, 481, 447]

fig, ax = plt.subplots(figsize=(3.4, 2.3))
bars = ax.bar(conditions, blocked_pct, width=0.5, color=colors,
              edgecolor="white", linewidth=0.6)
for b, n in zip(bars, totals):
    ax.annotate(f"{b.get_height():.1f}%", (b.get_x() + b.get_width() / 2,
                b.get_height()), ha="center", va="bottom", fontsize=7)
    ax.annotate(f"n={n}", (b.get_x() + b.get_width() / 2, -3.0),
                ha="center", va="top", fontsize=6, color="#555555")

ax.set_xlabel("Condition (A: no auditor, B: homogeneous, C: heterogeneous)",
              fontsize=6.5)
ax.set_ylabel("High-risk commands\nblocked (%)")
ax.set_ylim(-6, 24)
ax.spines["top"].set_visible(False)
ax.spines["right"].set_visible(False)
ax.axhline(0, color="#333333", linewidth=0.8)
fig.tight_layout()
fig.savefig("fig_blocking_by_condition.pdf")
plt.close(fig)

print("Wrote fig_success_by_condition.pdf and fig_blocking_by_condition.pdf")
