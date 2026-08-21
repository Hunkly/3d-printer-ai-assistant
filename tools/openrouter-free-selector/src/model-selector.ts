import { qualifyModel,type CatalogModel,type VerifiedFreeModel } from "./openrouter.js";
import { compatibilityStatus } from "./compatibility.js";
import type { CompatibilityEntry } from "./compatibility.js";
export type SelectorRole="plan"|"plan-review"|"build"|"review"|"readonly";
/**
 * Select the FIRST catalog record that satisfies ALL THREE eligibility gates:
 *  1. strict zero qualification (qualifyModel passes today's semantics)
 *  2. not excluded (producer exclusion set)
 *  3. currently compatible (compatibilityStatus === "current")
 *
 * Normal (non-override) selection: iterate catalog in original server order and
 * return the FIRST fully eligible candidate. Unknown / stale entries are skipped
 * during the scan. If no candidate is found, throw
 * NO_CURRENT_COMPATIBLE_FREE_MODEL.
 *
 * Explicit override: only the exact override model is considered. It must
 * satisfy all three gates; otherwise a deterministic failure is thrown.
 * No fallthrough. No alternative model.
 */
export function selectFirstCompatibleEligible(catalog:CatalogModel[],role:SelectorRole,entries:CompatibilityEntry[],excluded=new Set<string>(),override?:string,now=new Date()):VerifiedFreeModel {
  const normalized=override?.trim();
  if(normalized){
    const record=catalog.find(m=>m.id===normalized);
    if(!record)throw new Error("NO_VERIFIED_FREE_MODEL");
    const q=qualifyModel(record,now);
    if(!q||excluded.has(q.modelId))throw new Error("NO_VERIFIED_FREE_MODEL");
    const status=compatibilityStatus(entries,q.modelId,now);
    if(status==="unknown")throw new Error("CODEX_COMPATIBILITY_UNKNOWN");
    if(status==="stale")throw new Error("CODEX_COMPATIBILITY_STALE");
    return q;
  }
  for(const record of catalog){
    const q=qualifyModel(record,now);
    if(!q||excluded.has(q.modelId))continue;
    const status=compatibilityStatus(entries,q.modelId,now);
    if(status==="current")return q;
  }
  throw new Error("NO_CURRENT_COMPATIBLE_FREE_MODEL");
}
/** Preserved unchanged for backward compatibility and direct unit tests. */
export function selectFirstEligible(catalog:CatalogModel[],role:SelectorRole,excluded=new Set<string>(),override?:string,now=new Date()):VerifiedFreeModel {
  const normalized=override?.trim();const candidates=normalized?catalog.filter(m=>m.id===normalized):catalog;
  for(const record of candidates){const q=qualifyModel(record,now);if(q&&!excluded.has(q.modelId))return q;}
  throw new Error("NO_VERIFIED_FREE_MODEL");
}
export function roleForPhase(phase:string,target?:string):SelectorRole {if(phase==="plan")return "plan";if(phase==="build")return "build";if(phase==="review"&&target==="plan")return "plan-review";if(phase==="review"&&target==="implementation")return "review";if(phase==="general")return "readonly";throw new Error("INVALID_SELECTOR_ROLE");}