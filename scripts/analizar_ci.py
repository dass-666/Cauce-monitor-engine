"""Resume los fallos de la CI desde la API de GitHub (solo lectura): por job, por rama y por huella del error.
Uso: python scripts/analizar_ci.py [--runs 60]    (requiere `gh` autenticado). No modifica nada."""
import argparse
import collections
import json
import re
import subprocess

REPO = "dass-666/Cauce-monitor-engine"


def api(ruta, crudo=False):
    r = subprocess.run(["gh", "api", ruta], capture_output=True)
    return r.stdout.decode("utf-8", "replace") if crudo else json.loads(r.stdout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=60)
    a = ap.parse_args()
    runs = api(f"repos/{REPO}/actions/workflows/ci.yml/runs?per_page={a.runs}&status=completed")["workflow_runs"]
    tot, fallos, huellas = collections.Counter(), collections.Counter(), collections.Counter()
    por_sha = collections.defaultdict(list)
    for r in runs:
        jobs = api(f"repos/{REPO}/actions/runs/{r['id']}/jobs?per_page=100&filter=latest")["jobs"]
        malos = [j for j in jobs if j["conclusion"] == "failure"]
        por_sha[r["head_sha"][:7]].append((r["event"], r["run_attempt"], len(malos)))
        for j in jobs:
            if j["conclusion"] in ("success", "failure"):
                tot[j["name"]] += 1
                fallos[j["name"]] += j["conclusion"] == "failure"
        for j in malos:
            log = api(f"repos/{REPO}/actions/jobs/{j['id']}/logs", crudo=True)
            for l in log.splitlines():
                l = re.sub(r"^\S+\s", "", l)
                if re.search(r"FALLA|FALLO|AssertionError|ERR_[A-Z_]+|Timeout \d+ms|UnicodeEncodeError|ENOENT", l):
                    huellas[(j["name"].split("·")[0].strip(), re.sub(r"\d{2,}", "N", l)[:110])] += 1
                    break
    print("Jobs con fallos (fallos/ejecuciones):")
    for n, c in fallos.most_common():
        if c:
            print(f"  {c}/{tot[n]}  {n}")
    print("\nMismo commit, resultados distintos:")
    for sha, v in por_sha.items():
        if len({x[2] > 0 for x in v}) > 1:
            print(" ", sha, v)
    print("\nPrimera huella de error por job fallido:")
    for (j, h), k in huellas.most_common(12):
        print(f"  {k}  {j} | {h}")


if __name__ == "__main__":
    main()
