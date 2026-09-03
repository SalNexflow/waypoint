import { Container } from "./ui";

/* Figures come straight from the benchmark harness (bench/harness.py) and are
   quoted as measured. Each headline is stated against the strongest baseline
   on that metric, which is the harder comparison — greedy_nn is strongest on
   jobs done, cluster_nn on travel per job. */

const ROWS = [
  {
    strategy: "Greedy nearest-neighbour",
    note: "strongest baseline on jobs",
    jobs: "39.8",
    travel: "15.1 min",
  },
  {
    strategy: "Cluster then nearest-neighbour",
    note: "strongest baseline on travel",
    jobs: "35.9",
    travel: "13.1 min",
  },
  {
    strategy: "Waypoint",
    note: "120-second limit",
    jobs: "42.6",
    travel: "12.5 min",
    highlight: true,
  },
];

export default function Proof() {
  return (
    <section
      id="benchmark"
      aria-labelledby="benchmark-heading"
      className="on-dark bg-ink py-20 text-paper sm:py-28"
    >
      <Container>
        <p className="flex items-center gap-3 font-mono text-[11px] uppercase tracking-[0.14em] text-white/55">
          <span aria-hidden="true">05</span>
          <span aria-hidden="true" className="h-px w-6 bg-white/25" />
          <span>Benchmark</span>
        </p>

        <h2
          id="benchmark-heading"
          className="mt-5 max-w-3xl text-pretty text-2xl font-medium leading-[1.15] tracking-[-0.02em] sm:text-[32px]"
        >
          Waypoint is the only strategy that beats both baselines on both axes
          at once.
        </h2>

        <p className="mt-5 max-w-2xl text-[15px] leading-relaxed text-white/70">
          Greedy nearest-neighbour buys extra jobs with extra driving.
          Cluster-then-nearest-neighbour keeps driving low by doing fewer jobs.
          Waypoint does more jobs <em className="not-italic text-paper">and</em>{" "}
          less driving per job than either, which is a claim you can check by
          reading down two columns.
        </p>

        <div className="mt-14 grid gap-px border border-white/15 bg-white/15 sm:grid-cols-2">
          <div className="bg-ink p-6 sm:p-8">
            <p className="tabular text-[44px] font-medium leading-none tracking-[-0.035em] text-amber-bright sm:text-[56px]">
              +7.0%
            </p>
            <p className="mt-3 text-[15px] font-medium">more jobs completed</p>
            <p className="mt-1.5 text-[13px] leading-relaxed text-white/60">
              against greedy nearest-neighbour, the strongest baseline on jobs
              completed
            </p>
          </div>
          <div className="bg-ink p-6 sm:p-8">
            <p className="tabular text-[44px] font-medium leading-none tracking-[-0.035em] text-sky-bright sm:text-[56px]">
              +4.3%
            </p>
            <p className="mt-3 text-[15px] font-medium">better travel per job</p>
            <p className="mt-1.5 text-[13px] leading-relaxed text-white/60">
              against cluster-then-nearest-neighbour, the strongest baseline on
              travel per job
            </p>
          </div>
        </div>

        <div className="mt-10 overflow-x-auto">
          <table className="w-full min-w-[320px] border-collapse text-left">
            <caption className="sr-only">
              Average jobs completed and travel per job, by scheduling strategy.
            </caption>
            <thead>
              <tr className="border-b border-white/20">
                <th
                  scope="col"
                  className="py-2.5 pr-4 text-[11px] font-medium uppercase tracking-[0.08em] text-white/55"
                >
                  Strategy
                </th>
                <th
                  scope="col"
                  className="py-2.5 pr-4 text-right text-[11px] font-medium uppercase tracking-[0.08em] text-white/55"
                >
                  Jobs completed
                </th>
                <th
                  scope="col"
                  className="py-2.5 text-right text-[11px] font-medium uppercase tracking-[0.08em] text-white/55"
                >
                  Travel per job
                </th>
              </tr>
            </thead>
            <tbody>
              {ROWS.map((row) => (
                <tr
                  key={row.strategy}
                  className="border-b border-white/10 last:border-b-0"
                >
                  <th
                    scope="row"
                    className={`py-3.5 pr-4 text-[14px] font-normal ${
                      row.highlight ? "text-paper" : "text-white/75"
                    }`}
                  >
                    {row.strategy}
                    <span className="block text-[12px] text-white/45 sm:ml-2 sm:inline">
                      {row.note}
                    </span>
                  </th>
                  <td
                    className={`tabular py-3.5 pr-4 text-right font-mono text-[14px] ${
                      row.highlight ? "text-amber-bright" : "text-white/75"
                    }`}
                  >
                    {row.jobs}
                  </td>
                  <td
                    className={`tabular py-3.5 text-right font-mono text-[14px] ${
                      row.highlight ? "text-sky-bright" : "text-white/75"
                    }`}
                  >
                    {row.travel}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="mt-6 max-w-2xl text-[13px] leading-relaxed text-white/55">
          Benchmarked across 9 instances at 20/40/80 jobs, no traffic model.
        </p>
      </Container>
    </section>
  );
}
