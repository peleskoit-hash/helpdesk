const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const profile = fs.readFileSync('api/app/static/profile.js', 'utf8');
const quick = fs.readFileSync('api/app/static/quick_replies.js', 'utf8');
function between(source, start, end) {
  assert.ok(source.includes(start) && source.includes(end));
  return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
}

for (const failure of [null, 'crop', 'api', 'avatar']) {
  test(`avatar confirmation follows close and avatar refresh; failure=${failure}`, async () => {
    const order = [], errors = [];
    const panel = {};
    const failAt = phase => { order.push(phase); if (failure === phase) throw Error(phase); };
    const ctx = vm.createContext({
      cropState: {}, profile: {},
      el: {root: {closest(selector) { assert.equal(selector, '.panel.profile-panel'); return panel; }},
        avatarCropError: {}, saveAvatarCropBtn: {disabled: false}},
      FormData: class { append() {} },
      showModalError: (el, error) => { if (error) errors.push(error); },
      croppedAvatarBlob: async () => { failAt('crop'); return {blob: {}, extension: 'jpg'}; },
      api: async () => { failAt('api'); return {avatar_url: '/avatar'}; },
      avatarCacheBustedUrl: url => url,
      closeAvatarCropModal: () => order.push('close'),
      renderAvatar: () => failAt('avatar'),
      syncSidebarAvatar: () => order.push('sidebar'),
      showAvatarMessage() {}, setStatus() {}, avatarErrorMessage: error => error.message,
      window: {HelpdeskAppShell: {showSaved(target) { assert.equal(target, panel); order.push('saved'); }}},
    });
    vm.runInContext(between(profile, 'async function saveAvatarCrop()', 'async function deleteAvatar()'), ctx);
    await ctx.saveAvatarCrop();
    if (failure) {
      assert.ok(!order.includes('saved'));
      assert.deepEqual(errors, [failure]);
    } else assert.deepEqual(order, ['crop', 'api', 'close', 'avatar', 'sidebar', 'saved']);
    assert.equal(ctx.el.saveAvatarCropBtn.disabled, false);
    ctx.cropState = null;
    order.length = 0;
    await ctx.saveAvatarCrop();
    assert.deepEqual(order, []);
  });
}

for (const editing of [false, true]) {
  for (const failure of [null, 'api', 'load']) {
    test(`quick reply editing=${editing}, failure=${failure}: confirms only after close and load`, async () => {
      const order = [], errors = [], panel = {};
      let finishLoad;
      const ctx = vm.createContext({
        state: {editing: editing ? {id: 'reply', can_edit: true} : null, draftFiles: []},
        el: {save: {}, titleInput: {value: 'Title'}, bodyInput: {value: 'Body'},
          list: {closest(selector) { assert.equal(selector, '.panel.quick-replies-workspace'); return panel; }}},
        api: async (path, options) => {
          assert.equal(options.method, editing ? 'PATCH' : 'POST');
          assert.equal(path, editing ? '/api/v1/quick-replies/reply' : '/api/v1/quick-replies');
          order.push('api');
          if (failure === 'api') throw Error('api');
        },
        closeModal() { order.push('close'); ctx.state.editing = null; },
        load: () => {
          order.push('load');
          if (failure === 'load') throw Error('load');
          return new Promise(resolve => { finishLoad = resolve; });
        },
        renderDraftFiles: () => order.push('error-render'),
        window: {HelpdeskAppShell: {showSaved(target) { assert.equal(target, panel); order.push('saved'); }},
          HelpdeskErrorDialog: {show: error => errors.push(error)}},
      });
      vm.runInContext(between(quick, 'async function save()', 'async function uploadDraftFile'), ctx);
      const saving = ctx.save();
      await new Promise(setImmediate);
      assert.ok(!order.includes('saved'));
      if (!failure) { assert.deepEqual(order, ['api', 'close', 'load']); finishLoad(); }
      await saving;
      if (failure) {
        assert.ok(!order.includes('saved'));
        assert.equal(errors.length, 1);
        assert.equal(order.at(-1), 'error-render');
      } else assert.deepEqual(order, ['api', 'close', 'load', 'saved']);
      ctx.state.editing = {can_edit: false};
      order.length = 0;
      await ctx.save();
      assert.deepEqual(order, []);
    });
  }
}

test('login and password retain their separate relogin flow without saved feedback', () => {
  assert.doesNotMatch(between(profile, 'async function saveLogin()', 'async function savePassword()'), /showSaved/);
  assert.doesNotMatch(between(profile, 'async function savePassword()', 'const handleKeydown'), /showSaved/);
});
