const theme = document.querySelector('#theme');
function applyTheme(value) { if (!['light','dark','warm'].includes(value)) value='light'; document.documentElement.dataset.theme=value; theme.value=value; localStorage.setItem('cc-workbench-theme',value); }
theme.onchange=()=>applyTheme(theme.value);
applyTheme(localStorage.getItem('cc-workbench-theme')||'light');
const form = document.querySelector('#login-form');
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = form.querySelector('button');
  const error = document.querySelector('#login-error');
  button.disabled = true; error.hidden = true;
  try {
    const data = new FormData(form);
    const response = await fetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:data.get('username'), password:data.get('password')})});
    if (!response.ok) { let detail; try { detail = (await response.json()).detail; } catch {} throw new Error(typeof detail === 'string' ? detail : '登录失败，请检查账号和密码。'); }
    window.location.assign('/');
  } catch (e) { error.textContent = e.message || '连接失败，请稍后重试。'; error.hidden = false; }
  finally { button.disabled = false; }
});
