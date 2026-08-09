const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function loadTicketControls() {
  const source = fs.readFileSync('api/app/static/ticket_controls.js', 'utf8');
  const window = {};
  const context = vm.createContext({ window, console });
  vm.runInContext(source, context);
  return window.TicketControls;
}

function buttonStub() {
  return {
    classList: { toggle() {} },
    addEventListener(type, handler) {
      this.handlers = this.handlers || {};
      this.handlers[type] = handler;
    },
  };
}

test('greeting click with empty contact_name still calls greeting endpoint', async () => {
  const controls = loadTicketControls();
  const greetingBtn = buttonStub();
  const postedUrls = [];
  const errors = [];
  const statuses = [];

  controls.bindTicketControlHandlers({
    el: { greetingBtn },
    state: {
      ticketId: 'ticket-1',
      card: {
        client_profile: {
          contact_name: '',
          telegram_username: 'belfiss',
        },
      },
    },
    action() {},
    apiPost: async (url) => postedUrls.push(url),
    apiPatch: async () => {},
    loadCard: async () => {},
    setStatus: (message) => statuses.push(message),
    showError: (message) => errors.push(message),
    setVisible: () => {},
    openInstructionModal() {},
    closeInstructionModal() {},
    closeOnBackdropClick() {},
    assignCurrentTicket: async () => {},
    saveClientProfile: async () => {},
    openClientModal() {},
    closeClientModal() {},
  });

  await greetingBtn.handlers.click({ preventDefault: () => errors.push('preventDefault') });

  assert.deepEqual(postedUrls, ['/api/v1/tickets/ticket-1/greeting']);
  assert.deepEqual(errors, []);
  assert.equal(statuses[0], 'Отправляю приветствие...');
});
