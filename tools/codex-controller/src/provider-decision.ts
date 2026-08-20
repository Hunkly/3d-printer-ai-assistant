import type {ProviderMode} from "./core.js";
import {PREFERRED_MODEL} from "./core.js";
export const PRIMARY_SUBSCRIPTION_PLAN_TYPES=new Set(["go","plus","pro"]);
const DOCUMENTED_REACHED_TYPES=new Set(["rate_limit_reached","workspace_owner_credits_depleted","workspace_member_credits_depleted","workspace_owner_usage_limit_reached","workspace_member_usage_limit_reached"]);
export type Tri=true|false|"unknown";
export interface PrimaryStatus{auth:Tri;planSupported:Tri;quota:Tri;preferredModel:Tri;code?:string}
export function classifyPrimary(account:any,rate:any,models:any[]):PrimaryStatus{
  if(!account||account.account?.type!=="chatgpt"||account.requiresOpenaiAuth!==true)return{auth:false,planSupported:false,quota:"unknown",preferredModel:"unknown"};
  const auth=true,planSupported=PRIMARY_SUBSCRIPTION_PLAN_TYPES.has(account.account.planType);
  if(!planSupported)return{auth,planSupported:false,quota:"unknown",preferredModel:"unknown"};
  const byId=rate?.rateLimitsByLimitId;
  const snapshot=byId!==null&&byId!==undefined?(Object.hasOwn(byId,"codex")?byId.codex:undefined):rate?.rateLimits;
  if(!snapshot||snapshot.limitId!=="codex"||!("rateLimitReachedType" in snapshot))return{auth,planSupported,quota:"unknown",preferredModel:"unknown"};
  const windows=[snapshot.primary,snapshot.secondary].filter((x:any)=>x!==null&&x!==undefined);
  const valid=windows.every((w:any)=>Number.isFinite(w.usedPercent)&&w.usedPercent>=0&&w.usedPercent<=100);
  let quota:Tri="unknown";
  if(snapshot.rateLimitReachedType!==null&&!DOCUMENTED_REACHED_TYPES.has(snapshot.rateLimitReachedType))quota="unknown";
  else if(snapshot.rateLimitReachedType!==null||snapshot.spendControlReached===true||(valid&&windows.some((w:any)=>w.usedPercent>=100)))quota=false;
  else if(snapshot.rateLimitReachedType===null&&(snapshot.spendControlReached===false||snapshot.spendControlReached===null)&&snapshot.primary&&valid&&windows.every((w:any)=>w.usedPercent<100))quota=true;
  const preferredModel=models.some(m=>m.id===PREFERRED_MODEL&&m.model===PREFERRED_MODEL&&Array.isArray(m.inputModalities)&&m.inputModalities.includes("text"));
  return{auth,planSupported,quota,preferredModel};
}
export function decideProvider(mode:ProviderMode,status:PrimaryStatus):"primary"|"openrouter-free"{const safe=status.auth===true&&status.planSupported===true&&status.quota===true&&status.preferredModel===true;if(mode==="openrouter-free")return mode;if(mode==="primary"){if(!safe){if(status.auth===false||status.planSupported===false)throw new Error("PRIMARY_AUTH_UNAVAILABLE");if(status.quota===false)throw new Error("PRIMARY_QUOTA_UNAVAILABLE");if(status.preferredModel===false)throw new Error("PRIMARY_MODEL_UNAVAILABLE");throw new Error("PRIMARY_STATUS_UNKNOWN");}return"primary";}return safe?"primary":"openrouter-free";}
export function statusLines(mode:ProviderMode,status:PrimaryStatus|undefined,key:boolean){const skipped=mode==="openrouter-free",decision=skipped?"not-applicable":mode==="auto"?decideProvider(mode,status!):"not-applicable";const f=(x:Tri|undefined)=>x===undefined?"skipped":String(x);return[`provider_mode=${mode}`,`primary_auth_available=${f(skipped?undefined:status?.auth)}`,`primary_plan_supported=${f(skipped?undefined:status?.planSupported)}`,`primary_quota_available=${f(skipped?undefined:status?.quota)}`,`preferred_model=${PREFERRED_MODEL}`,`preferred_model_available=${f(skipped?undefined:status?.preferredModel)}`,`auto_decision=${decision}`,`openrouter_config_available=${decision==="primary"||mode==="primary"?"not-required":String(key)}`];}
