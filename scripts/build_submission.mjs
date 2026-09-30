import fs from 'node:fs/promises';
import path from 'node:path';
import { createRequire } from 'node:module';
import { pathToFileURL } from 'node:url';
const root = process.cwd();
const runtime = 'C:/Users/moham/.cache/codex-runtimes/codex-primary-runtime/dependencies';
process.env.RUNTIME_NODE_MODULES = `${runtime}/node/node_modules`;
process.env.RUNTIME_NODE = `${runtime}/node/bin/node.exe`;
const skill = 'C:/Users/moham/.codex/plugins/cache/openai-primary-runtime/presentations/26.905.11957/skills/presentations';
const require = createRequire(`${runtime}/node/node_modules/package.json`);
const { chromium } = require('playwright');
const { Presentation, PresentationFile } = await import(pathToFileURL(`${runtime}/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs`).href);
const { finalizePresentation } = await import(pathToFileURL(`${skill}/container_tools/artifact_tool_utils.mjs`).href);
const out = path.join(root, 'docs/submission');
const build = path.join(out, '.build');
await fs.mkdir(build, { recursive: true });
await fs.copyFile('C:/Users/moham/.codex/generated_images/01a0f1df-f61f-7c42-a93e-f892052a3f8f/exec-e2b1746c-49c4-41d6-a397-068edbbf2eb3.png', path.join(out,'cover.png'));
const browser = await chromium.launch({channel:'msedge',headless:true});
try {
  const page = await browser.newPage({viewport:{width:1440,height:1080},extraHTTPHeaders:{'ngrok-skip-browser-warning':'true'}});
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('https://dividable-fretted-aroma.ngrok-free.dev/voice', {waitUntil:'networkidle'});
  await page.screenshot({path:path.join(out,'voice-demo.png'),fullPage:true});
  await page.goto('https://dividable-fretted-aroma.ngrok-free.dev/voice/assets/judges.html', {waitUntil:'networkidle'});
  if (!(await page.locator('h1').innerText()).includes('A case you can try')) throw new Error('Judge guide missing');
  if(errors.length) throw new Error(errors.join('\n'));
  console.log('Public voice page and judge guide: PASS');
} finally { await browser.close(); }
await fs.copyFile(path.join(root,'.hosted/documents-complete-desktop.png'),path.join(out,'document-demo.png'));
const p = Presentation.create({slideSize:{width:1280,height:720}});
const ink='#1a1848', muted='#5e5b85', purple='#f6f4fb', amber='#a96800';
function text(s,value,x,y,w,h,size=28,color=ink,bold=false) {
  const t=s.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
  t.text=value; t.text.style={typeface:'Arial',fontSize:size,color,bold,autoFit:'none'}; return t;
}
async function pic(s,file,x,y,w,h) {s.images.add({blob:new Uint8Array(await fs.readFile(file)),contentType:'image/png',alt:path.basename(file),fit:'contain',position:{left:x,top:y,width:w,height:h}});}
const narrations=[];
function slide(title,narration,source='Project source and docs/CONVERSATION_ACCEPTANCE.md') {
 const s=p.slides.add();s.background.fill=purple;
 text(s,title,64,42,1152,96,48,ink,true);
 text(s,`Sawt al-Tameen   /   ${p.slides.items.length}`,64,670,950,24,16,muted);
 s.speakerNotes.textFrame.setText(`${narration}\n\nEvidence: ${source}`);narrations.push(narration);return s;
}
let s=slide('Sawt al-Tameen', 'Sawt al Tameen is the voice of insurance. It is an English language pre authorisation agent built on AssemblyAI. A clinic describes a request by voice, and the assistant prepares an auditable case for a qualified human reviewer. This presentation uses synthetic narration and fictional demonstration data.');
await pic(s,path.join(out,'cover.png'),0,0,1280,720);
s=slide('Pre-authorisation intake', 'A provider desk must collect the right member details, identify a treatment and understand which supporting documents are required. Missing information creates follow up work. Our prototype guides that intake conversation and makes the next step explicit. The intended users are insurer intake staff and provider authorisation desks.');
text(s,'The clinic needs a clear next step',64,185,1080,60,40,ink,true);
text(s,'A request starts with identity, procedure, cost and date.\n\nMissing documents need a specific follow-up.\n\nThe reviewer needs the case and its conversation.',64,280,1060,290,32);
s=slide('The browser voice demo', 'The public browser demo requires no account. Choose device speakers for echo protection, or headphones for spoken interruption. Start a call and speak the fictional provider and member identifiers. Live caller captions and assistant replies appear in one conversation. You can interrupt or type a correction when a detail is wrong.');
text(s,'Live conversation\n\nCaller captions\n\nTyped corrections\n\nEnglish and AED',64,185,340,340,30);
await pic(s,path.join(out,'voice-demo.png'),430,155,780,475);
s=slide('AssemblyAI and the case backend', 'AssemblyAI handles the hosted voice interaction through its Voice Agent API, including speech recognition, the managed conversational model and spoken output. Our server bridges twenty four kilohertz browser audio and executes three schema defined tools. Verification gates coverage checks. A deterministic rules engine reads the same benefit catalogue used for citations. Credentials stay on the server.');
text(s,'AssemblyAI Voice Agent API',64,180,1100,52,36,ink,true);
text(s,'Speech recognition, managed model, voice output and turn handling',64,239,1110,62,28,muted);
text(s,'FastAPI executes the business tools',64,338,1100,52,36,ink,true);
text(s,'verify_caller\ncheck_coverage_rule\nlog_transcript',64,404,475,175,30);
text(s,'Four synthetic policy tiers\n50 procedures and eight escalation rules\nRule citations and case audit history',565,404,610,175,29);
s=slide('Documents and a same-case recheck', 'This screenshot comes from the authenticated document acceptance run. The browser uploaded clinical notes, an operative plan and a prior treatment record, then downloaded matching bytes. In the actual AssemblyAI conversation, the assistant requested the missing documents and rechecked the same case after upload. The resulting recommendation still required human review. The document desk needs an operator access key.');
text(s,'Three required documents\n\nAuthenticated upload\n\nRequirements refresh\n\nSame case, new evaluation',64,175,370,365,29);
await pic(s,path.join(out,'document-demo.png'),450,155,765,475);
s=slide('Human decision authority', 'The agent cannot approve or deny a request because none of its three tools provides that action. A reviewer only API and the case state machine enforce the final decision boundary. For a voice linked case, sign off remains blocked until the completed transcript is stored. The system keeps the original recommendation and the human decision separately in the audit trail.');
text(s,'The voice agent prepares a recommendation',64,180,1120,60,38,ink,true);
text(s,'No approval or denial tool\n\nReviewer role required for a final decision\n\nCompleted transcript required before sign-off\n\nRecommendation and decision retained separately',64,277,1100,340,30);
s=slide('Evidence from the prototype', 'On September thirtieth, all three hundred and ninety three Python tests and forty six JavaScript browser and audio tests passed. Twenty six public deployment checks passed. Two actual AssemblyAI conversations with a synthetic spoken caller exercised document upload and recheck, and failed verification with a callback. These are small sample workflow results. Physical speaker echo and varied human accents still need evaluation, and full procedure readback remains inconsistent.');
text(s,'393',64,185,340,95,76,ink,true);text(s,'Python tests passed',64,290,410,52,27,muted);
text(s,'46',620,185,300,95,76,ink,true);text(s,'Browser and audio tests passed',620,290,580,52,27,muted);
text(s,'26 public deployment checks passed',64,392,1100,54,34,ink,true);
text(s,'Two completed AssemblyAI conversations with a synthetic caller',64,466,1110,64,28);
text(s,'Limits: procedure readback, physical echo and varied accents',64,568,1110,62,25,amber);
s=slide('Business value and the next pilot', 'Our business hypothesis is fewer incomplete handoffs and less repeated intake work. We would offer a paid pilot to an insurer desk, measuring handling time, document completeness, identifier accuracy and cost per completed case. There are no measured savings or clinical outcomes yet. The prototype uses only fictional policies and records. A live Twilio number is not configured, and the current demonstration host must stay online during judging.');
text(s,'A pilot for insurer intake teams',64,185,1100,60,40,ink,true);
text(s,'Measure handling time and repeated calls\n\nTrack document completeness and identifier accuracy\n\nCompare cost per completed case',64,280,1110,230,30);
text(s,'Business hypothesis. No measured customer savings yet.',64,566,1110,56,27,amber);
s=slide('Try the agent and inspect the source', 'You can test the voice agent online using the judge guide and fictional caller details. The public MIT licensed repository includes setup instructions, tests, the rules catalogue and conversation evidence. Sawt al Tameen brings a spoken request into an accountable review process. Prepared by the assistant, decided by a person.');
text(s,'Voice demo',64,190,1100,52,36,ink,true);
text(s,'dividable-fretted-aroma.ngrok-free.dev/voice',64,255,1120,50,29);
text(s,'Public source',64,365,1100,52,36,ink,true);
text(s,'github.com/adib-cyber007/sawt-al-tameen',64,430,1120,50,29);
text(s,'Prepared by the assistant. Decided by a person.',64,565,1120,54,32,ink,true);
const draft=path.join(build,'candidate.pptx');
await (await PresentationFile.exportPptx(p)).save(draft);
console.log('Draft deck exported');
for(let i=0;i<p.slides.items.length;i++){
 const blob=await p.export({slide:p.slides.items[i],format:'png',scale:1.5});
 await fs.writeFile(path.join(build,`slide-${i+1}.png`),new Uint8Array(await blob.arrayBuffer()));
 console.log(`Rendered slide ${i+1}`);
}
await fs.writeFile(path.join(build,'narrations.json'),JSON.stringify(narrations,null,2));
await fs.writeFile(path.join(out,'VIDEO_SCRIPT.md'),'# Presentation narration\n\nSynthetic narration accompanies actual application screenshots and documented acceptance results. It is not a recording of a live agent call.\n\n'+narrations.map((n,i)=>`## Slide ${i+1}\n\n${n}`).join('\n\n')+'\n');
await finalizePresentation({workspaceDir:root,candidatePath:draft,finalPath:path.join(out,'pitch-deck.pptx'),pythonExecutable:`${runtime}/python/python.exe`,integrityValidatorPath:`${skill}/container_tools/inspect_presentation_package_integrity.py`,layoutValidatorPath:`${skill}/container_tools/inspect_presentation_layout_geometry.py`,layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-heading-fit'],explicitTotalSlideCount:9,requiredNativeTableOwnerSlides:[],requiredNativeChartOwnerSlides:[],fontPolicy:{basis:'design',families:['Arial']},verifyArtifactToolImport:true,receiptPath:path.join(root,'.hosted/submission-deck-validation.json')});
console.log('Final PPTX validated');
