export const OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1";
export const MIN_CONTEXT_LENGTH = 32768;
export interface CatalogModel { id:string; pricing:Record<string,unknown>; architecture:Record<string,unknown>; context_length:number; top_provider?:Record<string,unknown>; expiration_date?:string|null; supported_parameters?:unknown[] }
export interface VerifiedFreeModel { modelId:string; record:CatalogModel }
const ZERO = /^0(?:\.0+)?(?:[eE][+-]?\d+)?$/;
const plain=(v:unknown):v is Record<string,unknown>=>v!==null&&typeof v==="object"&&!Array.isArray(v)&&Object.getPrototypeOf(v)===Object.prototype;
const REQUIRED_PRICING_KEYS=new Set(["prompt","completion"]);
const OPTIONAL_SCALAR_PRICING_KEYS=new Set(["request","image","web_search","internal_reasoning","input_cache_read","input_cache_write"]);
const RECOGNIZED_PRICING_KEYS=new Set([...REQUIRED_PRICING_KEYS,...OPTIONAL_SCALAR_PRICING_KEYS,"overrides"]);
export function isExactFreeId(id:string):boolean { return /^[^/,]+\/[^/,]+:free$/.test(id) && id!=="openrouter/free" && id!=="openrouter/auto"; }
export function isStrictZeroPricing(pricing:unknown):boolean {
  if (!plain(pricing)) return false;
  const p=pricing as Record<string,unknown>; const keys=Object.keys(p);
  if (![...REQUIRED_PRICING_KEYS].every(k=>Object.hasOwn(p,k))||keys.some(k=>!RECOGNIZED_PRICING_KEYS.has(k))) return false;
  if (![...REQUIRED_PRICING_KEYS].every(k=>typeof p[k]==="string"&&ZERO.test(p[k] as string))) return false;
  for(const key of OPTIONAL_SCALAR_PRICING_KEYS){if(Object.hasOwn(p,key)&&!(typeof p[key]==="string"&&ZERO.test(p[key] as string)))return false;}
  return !Object.hasOwn(p,"overrides")||(Array.isArray(p.overrides)&&p.overrides.length===0);
}
export function qualifyModel(value:unknown, now=new Date()):VerifiedFreeModel|null {
  if (!plain(value)) return null;
  const m=value as unknown as CatalogModel; if(typeof m.id!=="string"||!isExactFreeId(m.id)||!isStrictZeroPricing(m.pricing)) return null;
  if(!Number.isInteger(m.context_length)||m.context_length<MIN_CONTEXT_LENGTH) return null;
  const a=m.architecture; if(!a||!Array.isArray(a.input_modalities)||!a.input_modalities.includes("text")||!Array.isArray(a.output_modalities)||!a.output_modalities.includes("text")) return null;
  if(!Array.isArray(m.supported_parameters)||!m.supported_parameters.includes("tools")) return null;
  const providerContext=m.top_provider?.context_length; if(typeof providerContext!=="number"||!Number.isInteger(providerContext)||providerContext<MIN_CONTEXT_LENGTH) return null;
  if(m.expiration_date!==undefined&&m.expiration_date!==null){const t=Date.parse(m.expiration_date);if(!Number.isFinite(t)||t<=now.getTime())return null;}
  return {modelId:m.id,record:m};
}
export type FetchLike=(input:string,init?:RequestInit)=>Promise<Response>;
async function json(fetcher:FetchLike,url:string,key:string):Promise<unknown>{const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);try{const r=await fetcher(url,{headers:{Authorization:`Bearer ${key}`,Accept:"application/json"},signal:controller.signal});if(r.status!==200)throw new Error("OPENROUTER_REQUEST_FAILED");return await r.json();}catch{throw new Error("OPENROUTER_REQUEST_FAILED");}finally{clearTimeout(timer);}}
const ROLE:Record<string,string>={plan:"sort=intelligence-high-to-low&min_intelligence_index=0&min_agentic_index=0","plan-review":"sort=intelligence-high-to-low&min_intelligence_index=0&min_agentic_index=0",build:"sort=coding-high-to-low&min_coding_index=0&min_agentic_index=0",review:"sort=intelligence-high-to-low&min_coding_index=0"};
export function catalogUrl(role:string){const tail=ROLE[role];if(!tail)throw new Error("INVALID_SELECTOR_ROLE");return `${OPENROUTER_BASE_URL}/models?supported_parameters=tools&input_modalities=text&output_modalities=text&context=32768&max_price=0&max_output_price=0&${tail}`;}
export async function fetchCatalog(fetcher:FetchLike,key:string,role:string):Promise<CatalogModel[]>{const raw=await json(fetcher,catalogUrl(role),key);if(!plain(raw)||!Array.isArray(raw.data)||typeof raw.total_count!=="number"||!Number.isInteger(raw.total_count)||raw.total_count<0||raw.data.length!==raw.total_count)throw new Error("OPENROUTER_CATALOG_INCOMPLETE");const seen=new Set<string>();for(const item of raw.data){if(!plain(item)||typeof item.id!=="string"||!item.id||seen.has(item.id))throw new Error(plain(item)&&typeof item.id==="string"&&seen.has(item.id)?"OPENROUTER_CATALOG_DUPLICATE_MODEL":"OPENROUTER_CATALOG_INVALID");seen.add(item.id);}return raw.data as CatalogModel[];}
export async function preflightExactModel(fetcher:FetchLike,key:string,id:string,now=new Date()):Promise<VerifiedFreeModel>{if(!isExactFreeId(id))throw new Error("OPENROUTER_PREFLIGHT_FAILED");const [author,slug]=id.split("/");const raw=await json(fetcher,`${OPENROUTER_BASE_URL}/model/${encodeURIComponent(author)}/${encodeURIComponent(slug)}`,key) as any;if(!plain(raw)||!plain(raw.data)||raw.data.id!==id)throw new Error("OPENROUTER_PREFLIGHT_FAILED");const q=qualifyModel(raw.data,now);if(!q)throw new Error("OPENROUTER_PREFLIGHT_FAILED");return q;}
