const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.join(__dirname, "..");
const source = fs.readFileSync(path.join(root, "api", "app", "static", "settings_client_card.js"), "utf8");

function loadExplicitOptionRenames() {
  const match = source.match(/function explicitOptionRenames[\s\S]*?\n}\r?\n/);
  assert.ok(match, "explicitOptionRenames function should exist");
  const context = vm.createContext({ Set, Array, String });
  vm.runInContext(`${match[0]}; this.explicitOptionRenames = explicitOptionRenames;`, context);
  return context.explicitOptionRenames;
}

test("system select options stay editable and send explicit rename mapping", () => {
  assert.match(source, /editFieldOptions\.disabled = \(field\.field_type \|\| ""\) !== "select"/);
  assert.match(source, /editingFieldOriginalOptions/);
  assert.match(source, /function explicitOptionRenames/);
  assert.match(source, /payload\.option_renames = explicitOptionRenames/);
});

test("explicitOptionRenames is conservative set-diff logic", () => {
  const explicitOptionRenames = loadExplicitOptionRenames();
  const renames = (originalOptions, nextOptions) => JSON.parse(JSON.stringify(explicitOptionRenames(originalOptions, nextOptions)));
  assert.deepEqual(renames(["A", "B"], ["B", "A"]), []);
  assert.deepEqual(renames(["A", "B"], ["A", "C"]), [{ old_value: "B", new_value: "C" }]);
  assert.deepEqual(renames(["A", "B"], ["A", "B", "C"]), []);
  assert.deepEqual(renames(["A", "B", "C"], ["A", "B"]), []);
  assert.deepEqual(renames(["A", "B"], ["C", "D"]), []);
});
