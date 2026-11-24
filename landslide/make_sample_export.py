"""Create a SYNTHETIC sample results file so the parser can be demonstrated.
This is not real Landslide output; replace it with your own export."""
import csv
import random

random.seed(7)
with open("sample_landslide_export.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Interval", "Handover Attempts", "Handover Successes", "Avg Interruption Time (ms)"])
    for i in range(1, 21):
        attempts = 500
        successes = attempts - random.choice([0, 0, 0, 1, 1, 2, 3])
        w.writerow([f"{i * 30}s", attempts, successes, round(random.uniform(52, 88), 1)])
print("wrote sample_landslide_export.csv")
