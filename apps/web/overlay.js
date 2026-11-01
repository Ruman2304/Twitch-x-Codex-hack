const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=value=>{const seconds=Math.max(0,Math.floor(value));return `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;};
const attemptRounds=new Map();
// The capability stays in the fragment, never in request URLs or referrers.
const overlayToken=new URLSearchParams(location.hash.slice(1)).get('token')||'';
const liveSource=location.pathname==='/overlay/live';
let stopped=false,updating=false;
function unavailable(message){
  document.querySelector('#overlay-status').textContent=message;
  document.querySelector('#overlay-title').textContent='Overlay unavailable';
  document.querySelector('#overlay-subtitle').textContent='Create a new OBS link in DareLoop and update this browser source.';
  document.querySelector('#overlay-kicker').textContent='DARELOOP';
  document.querySelector('#overlay-options').innerHTML='';
  document.querySelector('#overlay-instruction').textContent='';
  document.querySelector('#overlay-timer').textContent='';
}
async function update(){if(stopped||updating)return;updating=true;try{
  const response=await fetch(liveSource?'/api/v1/overlay/state':'/api/v1/overlay/preview',{cache:'no-store',credentials:liveSource?'omit':'same-origin',headers:liveSource?{Authorization:`Bearer ${overlayToken}`}:{}});
  if(response.status===401||response.status===403){stopped=true;unavailable('LINK EXPIRED OR REVOKED');return;}
  if(!response.ok)throw new Error('offline');
  const data=(await response.json()).data;
  const mode=data.demo_mode?'DEMO':'TWITCH';
  document.querySelector('#overlay-status').textContent=`${mode} · ${data.session.status.toUpperCase()}`;
  const {round,attempt}=data;
  if(attempt&&!attemptRounds.has(attempt.id))attemptRounds.set(attempt.id,round?.id);
  const currentAttempt=attempt&&(attempt.round_id||attemptRounds.get(attempt.id))===round?.id?attempt:null;
  let options=[],title='The next move<br>starts with you.',subtitle='The streamer is reviewing the next challenge round.',kicker=`DARELOOP / ${mode}`,timer='WAITING';
  if(data.session.status==='ended'){title='This session has ended.';subtitle='The streamer will start a new session when ready.';timer='SESSION ENDED';
  }else if(data.session.status==='draft'){title='Getting ready.';
  }else if(round?.state==='voting'){
    options=round.options.map(option=>({...option,votes:data.tallies[option.number]||0}));title='Chat picks the next move.';subtitle='One vote per viewer. The streamer makes the final call.';timer=`VOTING · ${fmt(round.remaining_sec ?? (round.closes_at-Math.floor(Date.now()/1000)))}`;
  }else if(currentAttempt){title=esc(currentAttempt.title);kicker=`${mode} / CHALLENGE ${currentAttempt.status.toUpperCase()}`;subtitle=['running','paused'].includes(currentAttempt.status)?currentAttempt.instructions:'The streamer decides what happens next.';timer=['running','paused'].includes(currentAttempt.status)?`TIME LEFT · ${fmt(currentAttempt.remaining_sec)}`:currentAttempt.status.toUpperCase();
  }else if(round?.state==='resolved'&&round.winner){
    title=esc(round.winner.title);subtitle='Chat has made its pick. Waiting for the streamer to start.';kicker=`${mode} / COMMUNITY PICK`;timer='WINNER SELECTED';options=round.options.map(option=>({...option,votes:round.tallies?.[option.number]||0,winner:option.number===round.winner.number}));
  }
  if(data.session.status==='paused')timer=`PAUSED · ${timer}`;
  document.querySelector('#overlay-instruction').textContent=round?.state==='voting'?round.options.map(o=>`!vote ${o.number}`).join(' / ')+' in Twitch chat':currentAttempt?'The streamer can complete or skip.':'Chat picks. The streamer decides.';
  document.querySelector('#overlay-kicker').textContent=kicker;
  document.querySelector('#overlay-title').innerHTML=title;
  document.querySelector('#overlay-subtitle').textContent=subtitle;
  document.querySelector('#overlay-timer').textContent=timer;
  document.querySelector('#overlay-options').innerHTML=options.map(option=>`<div class="overlay-option ${option.winner?'winner':''}"><strong>${option.winner?'✓':option.number}</strong><span>${esc(option.title)}</span><span style="margin-left:auto;color:var(--lime)">${option.votes}</span></div>`).join('');
}catch{unavailable('CONNECTION LOST · RETRYING');}finally{updating=false;}}
update();setInterval(update,1000);
