import { qualifyModel,type CatalogModel,type VerifiedFreeModel } from "./openrouter.js";
export type SelectorRole="plan"|"plan-review"|"build"|"review";
export function selectFirstEligible(catalog:CatalogModel[],role:SelectorRole,excluded=new Set<string>(),override?:string,now=new Date()):VerifiedFreeModel {
  const normalized=override?.trim();const candidates=normalized?catalog.filter(m=>m.id===normalized):catalog;
  for(const record of candidates){const q=qualifyModel(record,now);if(q&&!excluded.has(q.modelId))return q;}
  throw new Error("NO_VERIFIED_FREE_MODEL");
}
export function roleForPhase(phase:string,target?:string):SelectorRole {if(phase==="plan")return "plan";if(phase==="build")return "build";if(phase==="review"&&target==="plan")return "plan-review";if(phase==="review"&&target==="implementation")return "review";throw new Error("INVALID_SELECTOR_ROLE");}
