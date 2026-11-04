'use strict';
async function run(path, method, body) {
  const buttons = document.querySelectorAll('button');
  buttons.forEach(button => button.disabled = true);
  try {
    const response = await fetch(path, {method, headers: {'Content-Type':'application/json'}, ...(body === undefined ? {} : {body})});
    const data = await response.json();
    document.getElementById('result-status').textContent = `${method} ${path} → HTTP ${response.status}`;
    document.getElementById('result').textContent = JSON.stringify(data, null, 2);
  } catch (error) {document.getElementById('result-status').textContent = error.message;}
  finally {buttons.forEach(button => button.disabled = false);}
}
document.getElementById('submit').addEventListener('click', () => run('/api/events','POST',document.getElementById('payload').value));
document.getElementById('read-state').addEventListener('click', () => run('/api/state','GET'));
document.getElementById('read-health').addEventListener('click', () => run('/health','GET'));
