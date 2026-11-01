const esc=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=value=>{const seconds=Math.max(0,Math.floor(value));return `${String(Math.floor(seconds/60)).padStart(2,'0')}:${String(seconds%60).padStart(2,'0')}`;};
const attemptRounds=new Map();
async function update(){try{
  const response=await fetch('/api/v1/state',{cache:'no-store'});if(!response.ok)throw new Error('offline');
  const data=(await response.json()).data;
  document.querySelector('#overlay-status').textContent=data.session.status==='ended'?'DEMO · ENDED':data.session.status==='live'?'DEMO SESSION':'DEMO · PAUSED';
  const {round,attempt}=data;
  if(attempt&&!attemptRounds.has(attempt.id))attemptRounds.set(attempt.id,round?.id);
  const currentAttempt=attempt&&(attempt.round_id||attemptRounds.get(attempt.id))===round?.id?attempt:null;
  let options=[],title='The next move<br>starts with you.',subtitle='The streamer is reviewing the next challenge round.',kicker='DARELOOP / LOCAL DEMO',timer='WAITING';
  if(round?.state==='voting'){
    options=round.options.map(option=>({...option,votes:data.tallies[option.number]||0}));title='Chat picks the next move.';subtitle='One vote per viewer. The streamer makes the final call.';timer=`VOTING · ${fmt(round.remaining_sec ?? (round.closes_at-Math.floor(Date.now()/1000)))}`;
  }else if(currentAttempt){title=esc(currentAttempt.title);kicker=`DEMO / CHALLENGE ${currentAttempt.status.toUpperCase()}`;subtitle=['running','paused'].includes(currentAttempt.status)?'Streamer approved. Complete or skip at any time.':'The streamer decides what happens next.';timer=['running','paused'].includes(currentAttempt.status)?`TIME LEFT · ${fmt(currentAttempt.remaining_sec)}`:currentAttempt.status.toUpperCase();
  }else if(round?.state==='resolved'){
    title=esc(round.winner.title);subtitle='Chat has made its pick. Waiting for the streamer to start.';kicker='DEMO / COMMUNITY PICK';timer='WINNER SELECTED';options=round.options.map(option=>({...option,votes:round.tallies?.[option.number]||0,winner:option.number===round.winner.number}));
  }
  document.querySelector('#overlay-kicker').textContent=kicker;
  document.querySelector('#overlay-title').innerHTML=title;
  document.querySelector('#overlay-subtitle').textContent=subtitle;
  document.querySelector('#overlay-timer').textContent=timer;
  document.querySelector('#overlay-options').innerHTML=options.map(option=>`<div class="overlay-option ${option.winner?'winner':''}"><strong>${option.winner?'✓':option.number}</strong><span>${esc(option.title)}</span><span style="margin-left:auto;color:var(--lime)">${option.votes}</span></div>`).join('');
}catch{document.querySelector('#overlay-status').textContent='DEMO · CONNECTION LOST · RETRYING';document.querySelector('#overlay-timer').textContent='OFFLINE · LAST KNOWN STATE';}}
update();setInterval(update,1000);
