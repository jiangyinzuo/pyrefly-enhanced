-- 运行：nvim --headless -u NONE -l tests/nvim_integration.lua
local api = vim.api
local root = vim.fn.fnamemodify(debug.getinfo(1, 'S').source:sub(2), ':p:h:h')
vim.opt.rtp:prepend(root)
vim.g.mapleader = ' '
vim.cmd('runtime plugin/pyrefly_enhanced.lua')
local directory = vim.fn.tempname()
vim.fn.mkdir(directory, 'p')
local path = directory .. '/demo.py'
local socket = directory .. '/nvim.sock'
local source = {
  'class Client:',
  '    @classmethod',
  '    def make(cls, argument=None):',
  '        return cls()',
  'class Demo:',
  '    def __init__(self, enabled: bool, client: Client):',
  '        if enabled:',
  '            self.client = Client.make(',
  "                argument='hidden long argument')",
  '        else:',
  '            self.client = client',
  '    def run(self):',
  '        return self.client',
}
vim.fn.writefile(source, path)
local client_id
local function check()
  vim.fn.serverstart(socket)
  vim.cmd.cd(directory)
  vim.cmd.edit(path)
  local source_buffer = api.nvim_get_current_buf()
  client_id = vim.lsp.start({ name = 'pyrefly',
    cmd = { 'pyrefly', 'lsp', '--indexing-mode', 'none', '--threads', '2' }, root_dir = directory })
  assert(vim.wait(30000, function()
    local client = vim.lsp.get_client_by_id(client_id)
    return client and client.initialized
  end, 50), 'LSP initialization timed out')
  vim.cmd('PyreflyEnhanced Demo')
  assert(vim.wait(60000, function()
    return api.nvim_buf_get_name(0) == 'pyrefly-enhanced://result'
  end, 50), 'Result tab did not open')
  local result = api.nvim_get_current_buf()
  local tab_count = #api.nvim_list_tabpages()
  assert(tab_count == 2, 'Expected one result tab')
  local lines = api.nvim_buf_get_lines(result, 0, -1, false)
  local target_line, target_text
  for index, text in ipairs(lines) do
    if text:find('self.client = Client.make(...)', 1, true) then
      target_line, target_text = index, text
    end
  end
  assert(target_line, 'Collapsed factory call missing')
  assert(not table.concat(lines, '\n'):find('hidden long argument', 1, true))
  for _, case in ipairs({ { 'client', 8 }, { 'Client', 1 }, { 'make', 3 } }) do
    api.nvim_win_set_buf(0, result)
    api.nvim_win_set_cursor(0, { target_line, target_text:find(case[1], 1, true) - 1 })
    local mapping = vim.fn.maparg(' gd', 'n', false, true)
    assert(mapping.buffer == 1 and mapping.callback, 'Local definition mapping missing')
    mapping.callback()
    assert(api.nvim_buf_get_name(0) == path, 'Wrong definition file for ' .. case[1])
    assert(api.nvim_win_get_cursor(0)[1] == case[2], 'Wrong definition line for ' .. case[1])
  end
  api.nvim_win_set_buf(0, result)
  vim.cmd('cd /tmp')
  vim.cmd('PyreflyEnhanced Client -all+method:cls')
  assert(vim.wait(60000, function()
    local text = table.concat(api.nvim_buf_get_lines(result, 0, -1, false), '\n')
    return text:find('class Client:', 1, true) and not text:find('class Demo:', 1, true)
  end, 50), 'Query from result buffer failed')
  assert(api.nvim_get_current_buf() == result and #api.nvim_list_tabpages() == tab_count,
    'Query created another result buffer or tab')
  assert(vim.bo[source_buffer].modified == false, 'Inspection changed source')
  local completions = vim.fn.getcompletion('PyreflyEnhanced Demo +member:pub:', 'cmdline')
  assert(vim.deep_equal(completions, { '+member:pub:self', '+member:pub:cls' }),
    'Completion must insert only one qualifier')
  print('PASS Neovim integration: real Pyrefly, initializer, cursor-sensitive gd, result reuse, completion')
end
local ok, err = xpcall(check, debug.traceback)
if client_id then
  local client = vim.lsp.get_client_by_id(client_id)
  if client then client:stop(true) end
end
pcall(vim.fn.serverstop, socket)
vim.fn.delete(directory, 'rf')
if not ok then
  io.stderr:write(err .. '\n')
  vim.cmd('cquit 1')
else
  vim.cmd('qa!')
end
