import {spawn,type ChildProcessWithoutNullStreams} from "node:child_process";
import {createRequire} from "node:module";
import {dirname,isAbsolute,resolve} from "node:path";
import {readFileSync} from "node:fs";
import {primaryEnvironment} from "./core.js";
import {classifyPrimary,type PrimaryStatus} from "./provider-decision.js";
export const INITIALIZE_REQUEST={id:1,method:"initialize",params:{clientInfo:{name:"print_engineer_codex_controller",title:"Print Engineer Codex Controller",version:"0.1.0"},capabilities:null}} as const;
export const INITIALIZED_NOTIFICATION={method:"initialized"} as const;
export type SpawnAppServer=(command:string,args:string[],options:any)=>ChildProcessWithoutNullStreams;
const unknown=(code:string):PrimaryStatus=>({auth:"unknown",planSupported:"unknown",quota:"unknown",preferredModel:"unknown",code});
function runtimePath(){const req=createRequire(import.meta.url),pkg=req.resolve("@openai/codex/package.json"),version=JSON.parse(readFileSync(pkg,"utf8")).version;if(version!=="0.147.0")throw new Error("PRIMARY_STATUS_START_FAILED");return resolve(dirname(pkg),"bin/codex.js");}
function validInitializeResult(value:unknown):boolean{if(!value||typeof value!=="object"||Array.isArray(value))return false;const result=value as Record<string,unknown>;return typeof result.userAgent==="string"&&result.userAgent.length>0&&typeof result.codexHome==="string"&&isAbsolute(result.codexHome)&&typeof result.platformFamily==="string"&&result.platformFamily.length>0&&typeof result.platformOs==="string"&&result.platformOs.length>0;}
export async function readPrimaryStatus(spawnFn:SpawnAppServer=spawn,deadlineMs=5000):Promise<PrimaryStatus>{
 let child:ChildProcessWithoutNullStreams;try{child=spawnFn(process.execPath,[runtimePath(),"app-server","--listen","stdio://","--strict-config"],{stdio:["pipe","pipe","pipe"],shell:false,env:primaryEnvironment(process.env)});}catch{return unknown("PRIMARY_STATUS_START_FAILED");}
 return new Promise(resolveStatus=>{let buffer="",expected=1,account:any,rate:any,pages=0,settled=false,candidate:PrimaryStatus|undefined,shutdown:NodeJS.Timeout|undefined;const models:any[]=[],cursors=new Set<string>(),seen=new Set<number>();let stderrBytes=0;
  const resolveOnce=(status:PrimaryStatus)=>{if(settled)return;settled=true;clearTimeout(deadline);if(shutdown)clearTimeout(shutdown);resolveStatus(status);};
  const complete=(status:PrimaryStatus)=>{candidate=status;clearTimeout(deadline);if(!child.stdin.writableEnded)child.stdin.end();shutdown??=setTimeout(()=>{child.kill();setTimeout(()=>resolveOnce(candidate!),0);},1000);};
  const protocol=()=>complete(unknown("PRIMARY_STATUS_PROTOCOL_ERROR"));
  const send=(value:unknown)=>{if(!candidate)child.stdin.write(JSON.stringify(value)+"\n");};
  const deadline=setTimeout(()=>{candidate=unknown("PRIMARY_STATUS_TIMEOUT");child.kill();resolveOnce(candidate);},deadlineMs);
  child.stderr.on("data",(chunk:Buffer|string)=>{stderrBytes=Math.min(8192,stderrBytes+Buffer.byteLength(chunk));});
  child.stdout.setEncoding("utf8");child.stdout.on("data",(chunk:string)=>{if(settled)return;buffer+=chunk;for(;;){const nl=buffer.indexOf("\n");if(nl<0)break;const line=buffer.slice(0,nl);buffer=buffer.slice(nl+1);let msg:any;try{msg=JSON.parse(line);}catch{protocol();continue;}if(!msg||typeof msg!=="object"||Array.isArray(msg)){protocol();continue;}if(msg.id===undefined){if(typeof msg.method!=="string")protocol();continue;}if(typeof msg.id!=="number"||seen.has(msg.id)){protocol();continue;}seen.add(msg.id);const hasResult=Object.hasOwn(msg,"result"),hasError=Object.hasOwn(msg,"error");if(msg.id!==expected||hasResult===hasError){protocol();continue;}
    if(expected===1){if(!hasResult||!validInitializeResult(msg.result)){protocol();continue;}send(INITIALIZED_NOTIFICATION);expected=2;send({id:2,method:"account/read",params:{refreshToken:false}});}
    else if(expected===2){account=msg.result;if(!account||typeof account!=="object"||Array.isArray(account)||!("account" in account)||typeof account.requiresOpenaiAuth!=="boolean"||(account.account!==null&&(typeof account.account!=="object"||typeof account.account.type!=="string"))){protocol();continue;}if(account.account?.type!=="chatgpt"){complete(classifyPrimary(account,undefined,[]));continue;}expected=3;send({method:"account/rateLimits/read",id:3});}
    else if(expected===3){if(!hasResult){protocol();continue;}rate=msg.result;if(classifyPrimary(account,rate,[]).quota==="unknown"){protocol();continue;}expected=4;send({id:4,method:"model/list",params:{includeHidden:true,limit:100}});}
    else{if(!hasResult){protocol();continue;}pages++;const page=msg.result;if(!page||typeof page!=="object"||Array.isArray(page)||!Array.isArray(page.data)||!(page.nextCursor===null||typeof page.nextCursor==="string")){protocol();continue;}let invalid=false;for(const model of page.data){if(!model||typeof model!=="object"||Array.isArray(model)||typeof model.id!=="string"||typeof model.model!=="string"||!Array.isArray(model.inputModalities)||!model.inputModalities.every((x:any)=>typeof x==="string")||((model.id==="gpt-5.6-sol"||model.model==="gpt-5.6-sol")&&model.id!==model.model)){invalid=true;break;}const prior=models.find(x=>x.id===model.id);if(prior&&JSON.stringify(prior)!==JSON.stringify(model)){invalid=true;break;}if(!prior)models.push(model);}if(invalid){protocol();continue;}if(page.nextCursor===null){complete(classifyPrimary(account,rate,models));continue;}if(!page.nextCursor||cursors.has(page.nextCursor)||pages>=100){protocol();continue;}cursors.add(page.nextCursor);expected++;send({id:expected,method:"model/list",params:{includeHidden:true,limit:100,cursor:page.nextCursor}});}
  }});
  child.on("error",()=>{candidate=unknown("PRIMARY_STATUS_START_FAILED");resolveOnce(candidate);});
  child.on("exit",()=>resolveOnce(candidate??unknown("PRIMARY_STATUS_PROTOCOL_ERROR")));
  send(INITIALIZE_REQUEST);
 });
}
