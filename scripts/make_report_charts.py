#!/usr/bin/env python3
"""Generates static Matplotlib figures for the written report (Phase 4's
"static comparison charts suitable for pasting into the final report/SRS").

Runs anywhere - no Mininet/Ryu needed, it only reads files produced earlier:
  models/training_log.csv        <- rl/train.py
  models/benchmark_results.json  <- scripts/benchmark.py

    python3 -m scripts.make_report_charts

Writes PNGs to docs/figures/. Charts whose source data is missing are
skipped with a message rather than drawn from invented numbers.
"""

import argparse
import csv
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402  (must follow matplotlib.use)

TRAINING_LOG = "models/training_log.csv"
BENCHMARK_JSON = "models/benchmark_results.json"
OUT_DIR = "docs/figures"

MODE_LABELS = {"rl": "RL agent", "dijkstra": "Dijkstra baseline"}
MODE_COLORS = {"rl": "#2f7fd1", "dijkstra": "#8a8f98"}


def _style(ax, title, xlabel, ylabel):
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_ylabel(ylabel, fontsize=10)
    ax.grid(True, alpha=0.25, linewidth=0.7)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def plot_reward_curve(training_log: str, out_dir: str) -> bool:
    if not os.path.exists(training_log):
        print(f"skip reward curve: {training_log} not found (run: python3 -m rl.train)")
        return False

    episodes, rewards = [], []
    with open(training_log, newline="") as handle:
        for row in csv.DictReader(handle):
            episodes.append(int(row["episode"]))
            rewards.append(float(row["avg_reward"]))

    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.plot(episodes, rewards, linewidth=0.9, color="#2f7fd1", label="RL agent (per episode)")

    # A rolling mean makes the trend legible under the per-episode noise.
    window = max(1, len(rewards) // 50)
    if window > 1:
        smoothed = [sum(rewards[max(0, i - window):i + 1]) / len(rewards[max(0, i - window):i + 1])
                    for i in range(len(rewards))]
        ax.plot(episodes, smoothed, linewidth=2.0, color="#14406e", label=f"rolling mean ({window} episodes)")

    _style(ax, "Q-learning reward convergence", "Episode", "Average reward per step")
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()

    path = os.path.join(out_dir, "reward_convergence.png")
    fig.savefig(path, dpi=200)
    plt.close(fig)
    print(f"wrote {path}")
    return True


def plot_benchmark(benchmark_json: str, out_dir: str) -> bool:
    if not os.path.exists(benchmark_json):
        print(f"skip comparison charts: {benchmark_json} not found (run: sudo python3 -m scripts.benchmark)")
        return False

    with open(benchmark_json) as handle:
        results = json.load(handle)

    modes = [m for m in ("dijkstra", "rl") if m in results.get("modes", {})]
    if not modes:
        print("skip comparison charts: benchmark file has no mode results")
        return False

    metrics = [
        ("avg_latency_ms", "Average latency", "ms", True),
        ("throughput_mbps", "Throughput", "Mbit/s", False),
        ("packet_loss_pct", "Packet loss", "%", True),
        ("recovery_time_s", "Recovery after failure", "seconds", True),
    ]
    metrics = [m for m in metrics if any(results["modes"][mode].get(m[0]) is not None for mode in modes)]
    if not metrics:
        print("skip comparison charts: no measured metrics in benchmark file")
        return False

    fig, axes = plt.subplots(1, len(metrics), figsize=(4.0 * len(metrics), 4.0))
    if len(metrics) == 1:
        axes = [axes]

    for ax, (key, title, unit, lower_better) in zip(axes, metrics):
        values = [results["modes"][mode].get(key) or 0 for mode in modes]
        labels = [MODE_LABELS.get(mode, mode) for mode in modes]
        colors = [MODE_COLORS.get(mode, "#8a8f98") for mode in modes]

        bars = ax.bar(labels, values, color=colors, width=0.55)
        for bar, value in zip(bars, values):
            ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                    f"{value:g}", ha="center", va="bottom", fontsize=9, fontweight="bold")

        direction = "lower is better" if lower_better else "higher is better"
        _style(ax, f"{title}\n({direction})", "", unit)
        ax.set_ylim(0, max(values) * 1.25 if max(values) else 1)
        ax.tick_params(axis="x", labelsize=9)

    fig.suptitle("RL vs Dijkstra baseline — identical traffic, measured in Mininet", fontsize=12, fontweight="bold")
    fig.tight_layout(rect=(0, 0, 1, 0.94))

    path = os.path.join(out_dir, "rl_vs_baseline.png")
    fig.savefig(path, dpi=200)
    plt.close(fig)
    print(f"wrote {path}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--training-log", default=TRAINING_LOG)
    parser.add_argument("--benchmark", default=BENCHMARK_JSON)
    parser.add_argument("--out-dir", default=OUT_DIR)
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    made = [plot_reward_curve(args.training_log, args.out_dir), plot_benchmark(args.benchmark, args.out_dir)]
    if not any(made):
        print("\nNothing generated - produce the source data first (see the messages above).")


if __name__ == "__main__":
    main()
