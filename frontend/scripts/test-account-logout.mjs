import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const accountSource = readFileSync(new URL('../src/pages/Account.tsx', import.meta.url), 'utf8');

function extractFunction(name) {
  const marker = `const ${name} = () => {`;
  const start = accountSource.indexOf(marker);
  assert.notEqual(start, -1, `${name} should exist`);
  const bodyStart = start + marker.length;
  const end = accountSource.indexOf('\n  };', bodyStart);
  assert.notEqual(end, -1, `${name} should have a function body`);
  return accountSource.slice(bodyStart, end);
}

function test_account_logout_uses_auth_context_and_login_redirect() {
  assert.match(accountSource, /useAuth\s*\(/, 'Account should read logout from AuthContext');
  const logoutBody = extractFunction('handleLogout');
  const switchBody = extractFunction('handleSwitchAccount');

  for (const [name, body] of [['handleLogout', logoutBody], ['handleSwitchAccount', switchBody]]) {
    assert.match(body, /\blogout\s*\(\s*\)/, `${name} should call AuthContext logout()`);
    assert.match(body, /window\.location\.href\s*=\s*['"]\/login['"]/, `${name} should hard navigate to /login`);
    assert.doesNotMatch(body, /navigate\s*\(\s*['"]\/login['"]/, `${name} should not use SPA navigate to /login`);
  }
}

function test_account_logout_does_not_only_clear_local_storage() {
  const logoutBody = extractFunction('handleLogout');
  const switchBody = extractFunction('handleSwitchAccount');

  for (const [name, body] of [['handleLogout', logoutBody], ['handleSwitchAccount', switchBody]]) {
    assert.doesNotMatch(body, /localStorage\.removeItem\s*\(\s*['"]access_token['"]\s*\)/, `${name} should not clear only localStorage`);
    assert.match(body, /\blogout\s*\(\s*\)/, `${name} should delegate storage cleanup to AuthContext logout()`);
  }
}

test_account_logout_uses_auth_context_and_login_redirect();
test_account_logout_does_not_only_clear_local_storage();
console.log('account logout regression tests passed');
