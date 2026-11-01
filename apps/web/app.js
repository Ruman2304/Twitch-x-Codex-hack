const $ = selector => document.querySelector(selector);
const ui = { data: null, toastTimer: null, busy: false, attemptRounds: new Map(), offline: false };
const esc = value => String(value).replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
const fmt = value => { const seconds = Math.max(0, Math.floor(value)); return `${String(Math.floor(seconds / 60)).padStart(2,'0')}:${String(seconds % 60).padStart(2,'0')}`; };
const timeAgo = stamp => { const seconds = Math.max(0, Math.floor(Date.now()/1000-stamp)); return seconds < 5 ? 'Just now' : seconds < 60 ? `${seconds}s ago` : `${Math.floor(seconds/60)}m ago`; };
function toast(message) { $('#toast').textContent=message; $('#toast').classList.add('show'); clearTimeout(ui.toastTimer); ui.toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),3500); }
async function request(path, body={}) {
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await response.json();
  if(!response.ok) throw new Error(typeof result.error==='string' ? result.error.replaceAll('_',' ') : 'The action could not be completed. Try again.');
  return result.data;
}
function render(data) {
  ui.data=data;
  const {session,challenges,round,tallies,attempt,scores,activity}=data;
  const paused=session.status==='paused';
  $('#sidebar-status').textContent=paused?'SESSION PAUSED':'DEMO SESSION';
  $('#session-pause').textContent=paused?'▶':'Ⅱ';
  $('#session-pause').setAttribute('aria-label',paused?'Resume demo session':'Pause demo session');
  const approved=challenges.filter(c=>c.approval_status==='approved');
  $('#approved-count').textContent=approved.length;
  $('#challenge-total').textContent=challenges.length;
  // Preserve focus and control nodes between polling snapshots.
  const challengeKey=JSON.stringify(challenges);
  if($('#challenge-list').dataset.key!==challengeKey) {
    $('#challenge-list').dataset.key=challengeKey;
    $('#challenge-list').innerHTML=challenges.map((c,index)=>`<article class="challenge-card"><div class="challenge-number">${String(index+1).padStart(2,'0')}</div><div class="challenge-main"><div class="challenge-title">${esc(c.title)}</div><div class="challenge-desc">${esc(c.instructions)}</div><div class="challenge-tags"><span class="tag">${esc(c.category)}</span><span class="tag">${c.duration_sec}s</span><span class="tag">${c.points} PTS</span><span class="tag ${c.approval_status==='approved'?'safe':c.approval_status==='rejected'?'rejected':'pending'}">${esc(c.approval_status.toUpperCase())}</span></div></div><div class="challenge-actions">${c.approval_status==='pending'?`<button class="small-action approve" data-approval="approved" data-id="${c.id}" aria-label="Approve ${esc(c.title)}">Approve ↗</button><button class="small-action reject" data-approval="rejected" data-id="${c.id}" aria-label="Reject ${esc(c.title)}">Reject</button>`:`<button class="small-action ${c.approval_status==='approved'?'reject':'approve'}" data-approval="${c.approval_status==='approved'?'rejected':'approved'}" data-id="${c.id}">${c.approval_status==='approved'?'Revoke':'Restore'}</button>`}</div></article>`).join('');
  }
  const voting=round?.state==='voting';
  const resolved=round?.state==='resolved';
  const total=Object.values(tallies).reduce((sum,n)=>sum+n,0);
  $('#vote-count').textContent=total;
  $('#audience-votes').textContent=total;
  $('#audience-meter').style.width=`${Math.min(100,total/3*100)}%`;
  $('#round-state').textContent=voting?'VOTING OPEN':resolved?'RESULT LOCKED':'STANDBY';
  const options=round?.options||[];
  $('#vote-options').innerHTML=options.length?options.map(option=>{const votes=tallies[option.number]||0;const winner=resolved&&round.winner?.number===option.number;return `<div class="vote-option ${winner?'winner':''}"><div class="vote-option-top"><span class="option-num">${winner?'✓':option.number}</span><span class="option-name">${esc(option.title)}</span><span class="option-votes">${votes} ${votes===1?'vote':'votes'}${winner?' · WINNER':''}</span></div><div class="vote-bar"><span style="width:${total?votes/total*100:0}%"></span></div></div>`;}).join(''):'<div class="empty-options"><span>↗</span><div><b>Three possibilities. One next move.</b><small>Approve at least two challenges below to open a vote.</small></div></div>';
  if(attempt&&!ui.attemptRounds.has(attempt.id)) {
    try { ui.attemptRounds.set(attempt.id,sessionStorage.getItem(`attempt-${attempt.id}`)||round?.id); } catch { ui.attemptRounds.set(attempt.id,round?.id); }
  }
  const currentAttempt=attempt&&ui.attemptRounds.get(attempt.id)===round?.id ? attempt : null;
  const running=currentAttempt?.status==='running';
  const finished=currentAttempt&&['completed','skipped','expired'].includes(currentAttempt.status);
  let title='LET CHAT<br>CHOOSE THE<br><span>NEXT MOVE.</span>',description='A little curiosity. A collective decision. Approve your challenges and let the adventure unfold.',label='READY TO LAUNCH',timer='—:——',timerLabel='WAITING FOR YOUR GO';
  const controls=['primary-action','close-vote','start-attempt','complete-attempt','skip-attempt'];
  controls.forEach(id=>$('#'+id).classList.add('hidden'));
  const show=id=>$('#'+id).classList.remove('hidden');
  if(voting) {title='THE NEXT MOVE<br>IS <span>UP TO CHAT.</span>';description='One current vote per viewer. They can change their pick before the clock runs out.';label='AUDIENCE VOTE IN PROGRESS';timer=fmt(round.closes_at-Math.floor(Date.now()/1000));timerLabel='VOTING CLOSES IN';show('close-vote');}
  else if(running) {title=esc(currentAttempt.title);description='Your challenge, your pace. Complete it or skip whenever you choose.';label='CHALLENGE IN PROGRESS';timer=fmt(currentAttempt.remaining_sec);timerLabel='TIME LEFT';show('complete-attempt');show('skip-attempt');}
  else if(finished) {title=currentAttempt.status==='completed'?'ONE FOR<br><span>THE FIELD NOTES.</span>':currentAttempt.status==='skipped'?'NEW DIRECTION.<br><span>SAME ADVENTURE.</span>':'TIME’S UP.<br><span>YOUR CALL.</span>';description=currentAttempt.status==='expired'?'The timer has ended. Mark the outcome, or skip and move on.':`Challenge ${currentAttempt.status}. Ready to let chat choose the next one?`;label=`CHALLENGE ${currentAttempt.status.toUpperCase()}`;timerLabel=currentAttempt.status.toUpperCase();if(currentAttempt.status==='expired'){show('complete-attempt');show('skip-attempt');}else show('primary-action');}
  else if(resolved) {title=esc(round.winner.title);description='Chat has made its pick. You decide when the challenge begins.';label='THE COMMUNITY HAS SPOKEN';timer='✓';timerLabel='WINNER SELECTED';show('start-attempt');}
  else show('primary-action');
  if(paused){label='SESSION MARKED PAUSED';description='Demo limitation: this pause changes session status; existing server timers continue.';$('#primary-action').classList.add('hidden');$('#start-attempt').classList.add('hidden');}
  $('#primary-action').disabled=approved.length<2||ui.busy||ui.offline;
  $('#primary-action').innerHTML=`${finished?'Open next round':'Open voting round'} <span>↗</span>`;
  $('#primary-action').title=approved.length<2?'Approve at least two challenges first':'';
  $('#arena-title').innerHTML=title;
  $('#arena-description').textContent=description;
  $('#round-label').textContent=label;
  $('#timer-value').textContent=timer;
  $('#timer-sub').textContent=timerLabel;
  $('#send-vote').disabled=!voting||paused||ui.busy||ui.offline;
  $('#vote-command').disabled=!voting||paused;
  if(!voting)$('#chat-result').textContent='Open a voting round to send a simulated vote.';
  else if(paused)$('#chat-result').textContent='Resume the demo session to send a vote here.';
  else if($('#chat-result').dataset.round!==round.id){$('#chat-result').textContent='Voting is open. Pick a viewer and send a command.';$('#chat-result').dataset.round=round.id;}
  const scoresKey=JSON.stringify(scores);
  if($('#leaderboard-list').dataset.key!==scoresKey){$('#leaderboard-list').dataset.key=scoresKey;$('#leaderboard-list').innerHTML=scores.length?scores.map((score,index)=>`<div class="leader-row"><span class="rank">${String(index+1).padStart(2,'0')}</span><span class="person-avatar">${esc(score.name.slice(0,1))}</span><span class="person-name">${esc(score.name)}</span><span class="person-points">${score.points} pts</span></div>`).join(''):'<div class="empty-state">Your first voters set the pace.</div>';}
  $('#activity-list').innerHTML=activity.length?activity.slice(0,4).map(item=>`<div class="activity-row"><i class="activity-dot"></i><div>${esc(item.message)}<time>${timeAgo(item.at)}</time></div></div>`).join(''):'<div class="empty-state">Your session story starts here.</div>';
}
async function refresh(){try{const response=await fetch('/api/v1/state',{cache:'no-store'});if(!response.ok)throw new Error('state unavailable');const result=await response.json();ui.offline=false;render(result.data);$('#sync-status').textContent='● CONNECTED TO LOCAL DEMO';}catch{ui.offline=true;$('#sync-status').textContent='○ CONNECTION LOST · RETRYING';$('#send-vote').disabled=true;$('#primary-action').disabled=true;}}
async function action(path,body={},message='Updated.') {
  if(ui.busy)return;
  ui.busy=true;
  try{const data=await request(path,body);if(path==='/api/v1/attempt'&&data?.attempt){ui.attemptRounds.set(data.attempt.id,data.round.id);try{sessionStorage.setItem(`attempt-${data.attempt.id}`,data.round.id);}catch{}}
    ui.busy=false;if(data)render(data);toast(message);
  }catch(error){toast(error.message);}finally{ui.busy=false;}
}
$('#challenge-list').addEventListener('click',event=>{const button=event.target.closest('[data-approval]');if(!button)return;if(ui.data?.round?.state==='voting'){toast('Close the current vote before changing its approved pool.');return;}action(`/api/v1/challenges/${button.dataset.id}/approval`,{approval:button.dataset.approval},button.dataset.approval==='approved'?'Challenge approved. Your call, always.':'Challenge removed from the approved pool.');});
$('#primary-action').onclick=()=>action('/api/v1/rounds',{},'Voting is open for 60 seconds. Let chat choose.');
$('#close-vote').onclick=()=>action('/api/v1/rounds/close',{},'Votes locked. The next move is yours.');
$('#start-attempt').onclick=()=>action('/api/v1/attempt',{},'The clock is running. You can always skip.');
$('#complete-attempt').onclick=()=>action('/api/v1/attempt/action',{action:'complete'},'Another one for the field notes.');
$('#skip-attempt').onclick=()=>action('/api/v1/attempt/action',{action:'skip'},'Skipped. There’s always another direction.');
$('#session-pause').onclick=()=>action('/api/v1/session/pause',{},'Demo status updated. Existing server timers continue.');
$('#send-vote').onclick=async()=>{if(ui.busy||$('#send-vote').disabled)return;const match=$('#vote-command').value.trim().match(/^!vote\s+([1-3])$/i);if(!match){$('#chat-result').textContent='Try !vote 1, !vote 2, or !vote 3.';return;}ui.busy=true;$('#send-vote').disabled=true;try{const data=await request('/api/v1/demo/vote',{viewer:$('#viewer-select').value,option:Number(match[1])});ui.busy=false;render(data);$('#chat-result').textContent=`${$('#viewer-select').selectedOptions[0].text}: vote counted for option ${match[1]}.`;}catch(error){$('#chat-result').textContent=error.message;}finally{ui.busy=false;if(ui.data)render(ui.data);}};
$('#vote-command').addEventListener('keydown',event=>{if(event.key==='Enter')$('#send-vote').click();});
$('#overlay-link').onclick=()=>window.open('/overlay/demo','dareloop-overlay','noopener');
$('#copy-overlay').onclick=async()=>{const url=new URL('/overlay/demo',location.origin).href;try{await navigator.clipboard.writeText(url);toast('Overlay URL copied. Add it as an OBS browser source.');}catch{toast(`Overlay: ${url}`);}};
$('#add-challenge').onclick=()=>action('/api/v1/challenges/curated',{},'A curated challenge is ready for your review.');
$('#reset-demo').onclick=()=>{if(confirm('Reset this demo’s challenges, votes, timers, and points?')){ui.attemptRounds.clear();action('/api/v1/demo/reset',{},'Fresh slate. Make a moment of it.');}};
const menu=$('.mobile-menu');
function closeMenu(){$('.sidebar').classList.remove('mobile-open');menu.setAttribute('aria-expanded','false');menu.setAttribute('aria-label','Open navigation');}
menu.onclick=()=>{const open=$('.sidebar').classList.toggle('mobile-open');menu.setAttribute('aria-expanded',String(open));menu.setAttribute('aria-label',open?'Close navigation':'Open navigation');};
document.addEventListener('keydown',event=>{if(event.key==='Escape')closeMenu();});
document.addEventListener('click',event=>{if(!event.target.closest('.sidebar')&&!event.target.closest('.mobile-menu'))closeMenu();});
document.querySelectorAll('.nav-item[href]').forEach(link=>link.addEventListener('click',()=>{document.querySelectorAll('.nav-item').forEach(item=>item.classList.remove('active'));link.classList.add('active');closeMenu();}));
async function loadTwitchAccount(){try{const response=await fetch('/api/v1/me',{cache:'no-store'});if(!response.ok)throw new Error('account unavailable');const data=(await response.json()).data;$('#twitch-status').textContent=data.connected?`SIGNED IN · ${data.user.display_name.toUpperCase()}`:'TWITCH NOT CONNECTED';$('#creator-name').textContent=data.connected?data.user.display_name:'Demo creator';$('#twitch-connect').classList.toggle('hidden',data.connected);$('#twitch-disconnect').classList.toggle('hidden',!data.connected);}catch{$('#twitch-status').textContent='ACCOUNT STATUS UNAVAILABLE';}}
$('#twitch-disconnect').onclick=async()=>{try{await request('/api/v1/auth/twitch/disconnect');await loadTwitchAccount();toast('Twitch account disconnected.');}catch(error){toast(error.message);}};
refresh();loadTwitchAccount();setInterval(()=>{if(!document.hidden&&!ui.busy)refresh();},1000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
