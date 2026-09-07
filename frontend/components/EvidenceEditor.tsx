"use client";

import { useEffect, useId, useState } from "react";
import {
  api,
  emptyReaction,
  emptySource,
  EvidenceSource,
  Preflight,
  usableSources,
} from "@/lib/api";

type Props = {
  materialName: string;
  sources: EvidenceSource[];
  onChange: (sources: EvidenceSource[]) => void;
  onPreflight?: (preflight: Preflight | null) => void;
};

export default function EvidenceEditor({ materialName, sources, onChange, onPreflight }: Props) {
  const fieldId = useId();
  const [preflight, setPreflight] = useState<Preflight | null>(null);
  const [checking, setChecking] = useState(false);
  const [problem, setProblem] = useState("");

  const target = materialName.trim();
  const ready = usableSources(sources);
  const serialized = JSON.stringify({ target, ready });

  useEffect(() => {
    if (!target || ready.length === 0) {
      setPreflight(null);
      setProblem("");
      onPreflight?.(null);
      return;
    }
    let active = true;
    setChecking(true);
    const timer = window.setTimeout(() => {
      api<Preflight>("/retrosynthesis/preflight", {
        method: "POST",
        body: JSON.stringify({ material_name: target, sources: ready }),
      })
        .then(value => {
          if (!active) return;
          setPreflight(value);
          setProblem("");
          onPreflight?.(value);
        })
        .catch(reason => {
          if (!active) return;
          setPreflight(null);
          setProblem(reason instanceof Error ? reason.message : "Could not check the evidence");
          onPreflight?.(null);
        })
        .finally(() => active && setChecking(false));
    }, 500);
    return () => { active = false; window.clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serialized]);

  function update(index: number, next: Partial<EvidenceSource>) {
    onChange(sources.map((source, position) => (position === index ? { ...source, ...next } : source)));
  }

  function updateReaction(sourceIndex: number, reactionIndex: number, field: string, value: string) {
    update(sourceIndex, {
      reactions: sources[sourceIndex].reactions.map((reaction, position) =>
        position === reactionIndex ? { ...reaction, [field]: value } : reaction
      ),
    });
  }

  return (
    <section className="evidence">
      <div className="evidence-head">
        <div>
          <h3>Synthesis evidence <span className="optional-tag">optional</span></h3>
          <p className="field-help">
            The route is planned for you and checked against the reaction graph. Add reactions here only to
            override that with literature you have chosen; at least one of them must produce the target.
          </p>
        </div>
        {target && <span className="evidence-target">Target: <code>{target}</code></span>}
      </div>

      {sources.map((source, sourceIndex) => (
        <article className="evidence-source" key={sourceIndex}>
          <div className="evidence-source-head">
            <label>
              Source
              <input
                value={source.name}
                onChange={event => update(sourceIndex, { name: event.target.value })}
                placeholder="Smith et al. 2019, J. Polym. Sci."
                maxLength={240}
              />
            </label>
            {sources.length > 1 && (
              <button
                type="button"
                className="quiet remove"
                onClick={() => onChange(sources.filter((_, position) => position !== sourceIndex))}
              >
                Remove source
              </button>
            )}
          </div>

          {source.reactions.map((reaction, reactionIndex) => (
            <div className="evidence-reaction" key={reactionIndex}>
              <span className="evidence-index">{reactionIndex + 1}</span>
              <div className="evidence-fields">
                <label>
                  Reactants
                  <span className="field-help">Separate multiple reactants with commas</span>
                  <input
                    value={reaction.reactants}
                    onChange={event => updateReaction(sourceIndex, reactionIndex, "reactants", event.target.value)}
                    placeholder="ethylene oxide, water"
                  />
                </label>
                <div className="evidence-field">
                  <div className="evidence-field-head">
                    <label htmlFor={`${fieldId}-${sourceIndex}-${reactionIndex}-products`}>Products</label>
                    {target && (
                      <button
                        type="button"
                        className="inline-insert"
                        onClick={() => updateReaction(sourceIndex, reactionIndex, "products", target)}
                      >
                        Use target
                      </button>
                    )}
                  </div>
                  <input
                    id={`${fieldId}-${sourceIndex}-${reactionIndex}-products`}
                    value={reaction.products}
                    onChange={event => updateReaction(sourceIndex, reactionIndex, "products", event.target.value)}
                    placeholder={target || "poly(ethylene glycol)"}
                  />
                </div>
                <label>
                  Conditions
                  <span className="field-help">Optional</span>
                  <input
                    value={reaction.conditions}
                    onChange={event => updateReaction(sourceIndex, reactionIndex, "conditions", event.target.value)}
                    placeholder="KOH, 120 C, 6 h"
                  />
                </label>
              </div>
              {source.reactions.length > 1 && (
                <button
                  type="button"
                  className="quiet remove"
                  onClick={() =>
                    update(sourceIndex, {
                      reactions: source.reactions.filter((_, position) => position !== reactionIndex),
                    })
                  }
                >
                  Remove
                </button>
              )}
            </div>
          ))}

          <button
            type="button"
            className="quiet add-reaction"
            onClick={() => update(sourceIndex, { reactions: [...source.reactions, emptyReaction()] })}
          >
            + Add reaction
          </button>
        </article>
      ))}

      <button type="button" className="quiet" onClick={() => onChange([...sources, emptySource()])}>
        + Add source
      </button>

      <div className="evidence-status" aria-live="polite">
        {checking && <p className="muted">Checking evidence against the retrosynthesis engine…</p>}
        {problem && <p className="error">{problem}</p>}
        {!checking && !problem && preflight && (
          <>
            <p className={preflight.root_product_found ? "evidence-ok" : "error"}>
              {preflight.root_product_found
                ? `Root product found — ${preflight.paper_count} source${preflight.paper_count === 1 ? "" : "s"} ready`
                : `No reaction lists "${preflight.tree_root}" as a product. The tree has no root, so this will be rejected.`}
            </p>
            {preflight.blocking_reactants.length > 0 && (
              <p className="warning">
                Not in the purchasable database: {preflight.blocking_reactants.join(", ")}. Add upstream
                reactions for these, or the route will not terminate.
              </p>
            )}
          </>
        )}
        {!checking && !problem && !preflight && ready.length === 0 && (
          <p className="muted">Add a source with at least one reactant and one product to check it.</p>
        )}
      </div>
    </section>
  );
}
