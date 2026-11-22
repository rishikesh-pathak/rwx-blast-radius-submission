"""Deterministic worker scheduling with the scorer's startup charge."""

from heapq import heapify, heapreplace

WORKER_COST = 2000
MAX_WORKERS = 8


def pack(selected, durations):
    jobs = sorted(set(selected), key=lambda job: (-durations[job], job))
    if not jobs:
        return []
    total = sum(durations[job] for job in jobs)
    longest = durations[jobs[0]]
    best = [jobs]
    best_cost = total + WORKER_COST
    for count in range(2, min(MAX_WORKERS, len(jobs)) + 1):
        overhead = count * WORKER_COST
        if longest + overhead >= best_cost:
            break
        if (total + count - 1) // count + overhead >= best_cost:
            continue
        workers = [[job] for job in jobs[:count]]
        loads = [(durations[job], i) for i, job in enumerate(jobs[:count])]
        heapify(loads)
        for job in jobs[count:]:
            load, worker = loads[0]
            workers[worker].append(job)
            heapreplace(loads, (load + durations[job], worker))
        cost = max(load for load, _ in loads) + overhead
        if cost < best_cost:
            best, best_cost = workers, cost
    return best
