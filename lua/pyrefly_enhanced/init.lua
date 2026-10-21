local M = {}
local api = vim.api
local options = { python = 'python3', timeout = 60 }
local plugin_root = vim.fn.fnamemodify(debug.getinfo(1, 'S').source:sub(2), ':h:h:h')
local result_buffer
local result_tab
local sequence = 0
local process

local function absolute(path)
  return vim.fs.normalize(vim.fn.fnamemodify(path, ':p'))
end

local function contains(root, path)
  root = absolute(root):gsub('/+$', '')
  path = absolute(path)
  return path == root or path:sub(1, #root + 1) == root .. '/'
end

local function buffer_for(path)
  path = absolute(path)
  for _, bufnr in ipairs(api.nvim_list_bufs()) do
    if api.nvim_buf_is_valid(bufnr) and api.nvim_buf_get_name(bufnr) ~= ''
      and absolute(api.nvim_buf_get_name(bufnr)) == path then
      if not api.nvim_buf_is_loaded(bufnr) and vim.fn.filereadable(path) ~= 1 then
        error('Cannot read source: ' .. path)
      end
      vim.cmd('noautocmd call bufload(' .. bufnr .. ')')
      return bufnr
    end
  end
  if vim.fn.filereadable(path) ~= 1 then
    error('Cannot read source: ' .. path)
  end
  local bufnr = vim.fn.bufadd(path)
  vim.bo[bufnr].swapfile = false
  -- 不切换窗口；禁用读入自动命令，避免隐式启动新的 LSP。
  vim.cmd('noautocmd call bufload(' .. bufnr .. ')')
  if not api.nvim_buf_is_loaded(bufnr) then
    error('Cannot load source: ' .. path)
  end
  return bufnr
end

function M.source(path)
  local bufnr = buffer_for(path)
  local text = table.concat(api.nvim_buf_get_lines(bufnr, 0, -1, false), '\n')
  if vim.bo[bufnr].endofline then
    text = text .. '\n'
  end
  return text
end

function M.buffers(root)
  local paths = {}
  for _, bufnr in ipairs(api.nvim_list_bufs()) do
    local path = api.nvim_buf_get_name(bufnr)
    if api.nvim_buf_is_loaded(bufnr) and path ~= '' and contains(root, path)
      and (vim.bo[bufnr].filetype == 'python' or path:match('%.pyi?$')) then
      paths[#paths + 1] = absolute(path)
    end
  end
  table.sort(paths)
  return paths
end

local function roots(client)
  local result = {}
  local root = client.root_dir or client.config.root_dir
  if type(root) == 'string' and root ~= '' then
    result[#result + 1] = root
  end
  for _, folder in ipairs(client.workspace_folders or {}) do
    if folder.uri then
      result[#result + 1] = vim.uri_to_fname(folder.uri)
    end
  end
  return result
end

local function pyrefly(client)
  return client.name:lower():find('pyrefly', 1, true) ~= nil
end

local function select_client(bufnr)
  local path = api.nvim_buf_get_name(bufnr)
  local candidates = {}
  local best = -1
  for _, client in ipairs(vim.lsp.get_clients()) do
    if pyrefly(client) and client.initialized and not client:is_stopped() then
      local score = -1
      for _, root in ipairs(roots(client)) do
        if contains(root, path) then
          score = math.max(score, #absolute(root))
        end
      end
      if vim.lsp.buf_is_attached(bufnr, client.id) then
        score = 1000000 + math.max(score, 0)
      end
      if score >= 0 then
        if score > best then
          best, candidates = score, { client }
        elseif score == best then
          candidates[#candidates + 1] = client
        end
      end
    end
  end
  if #candidates == 0 then
    error('No initialized pyrefly client attached to or covering ' .. path)
  end
  if #candidates > 1 then
    local names = {}
    for _, client in ipairs(candidates) do
      names[#names + 1] = client.name .. '#' .. client.id
    end
    error('Ambiguous pyrefly clients for ' .. path .. ': ' .. table.concat(names, ', '))
  end
  local client = candidates[1]
  if not client:supports_method('textDocument/completion', bufnr) then
    error('Pyrefly does not support completion for ' .. path)
  end
  -- didOpen 需要 Python languageId，但 FileType 自动命令可能另启服务器。
  if vim.bo[bufnr].filetype == '' and path:match('%.pyi?$') then
    vim.cmd('noautocmd call setbufvar(' .. bufnr .. ', "&filetype", "python")')
  end
  if not vim.lsp.buf_is_attached(bufnr, client.id)
    and not vim.lsp.buf_attach_client(bufnr, client.id) then
    error('Cannot attach existing pyrefly client to ' .. path)
  end
  return client
end

local function type_detail(item)
  local detail = item.detail
  if type(detail) ~= 'string' or detail == '' then
    detail = item.labelDetails and item.labelDetails.detail
  end
  if type(detail) == 'string' and detail ~= '' then
    return detail
  end
end

-- RPC 坐标固定为零基 UTF-16；转换为该客户端协商的编码。
function M.complete(path, line, column, timeout_ms)
  local bufnr = buffer_for(path)
  local client = select_client(bufnr)
  if type(line) ~= 'number' or line < 0 or line % 1 ~= 0
    or line >= api.nvim_buf_line_count(bufnr) then
    error('Completion line is outside source buffer')
  end
  if type(column) ~= 'number' or column < 0 or column % 1 ~= 0 then
    error('Completion column must be a nonnegative UTF-16 offset')
  end
  local text = api.nvim_buf_get_lines(bufnr, line, line + 1, false)[1]
  local byte = vim.str_byteindex(text, 'utf-16', column, true)
  local encoding = client.offset_encoding or 'utf-16'
  local character = vim.str_utfindex(text, encoding, byte, true)
  local tick = api.nvim_buf_get_changedtick(bufnr)
  local timeout = timeout_ms or 10000
  local deadline = (vim.uv or vim.loop).hrtime() / 1e6 + timeout
  local function request(method, params)
    local remaining = math.floor(deadline - (vim.uv or vim.loop).hrtime() / 1e6)
    if remaining <= 0 then
      error('Pyrefly completion timed out')
    end
    -- request_sync 经 Client:request 刷新待发送 didChange，不能直接调用 rpc.request。
    local response, err = client:request_sync(method, params, remaining, bufnr)
    if not response then
      error('Pyrefly ' .. method .. ' failed: ' .. tostring(err))
    end
    if response.err then
      error('Pyrefly ' .. method .. ': ' .. (response.err.message or vim.inspect(response.err)))
    end
    if not api.nvim_buf_is_valid(bufnr) or api.nvim_buf_get_changedtick(bufnr) ~= tick then
      error('Source changed during pyrefly completion: ' .. path)
    end
    return response.result
  end
  local response = request('textDocument/completion', {
    textDocument = { uri = vim.uri_from_bufnr(bufnr) },
    position = { line = line, character = character },
    context = { triggerKind = 1 },
  })
  local result = vim.empty_dict()
  if not response or response == vim.NIL then
    return result
  end
  local resolve = client:supports_method('completionItem/resolve', bufnr)
  for _, item in ipairs(response.items or response) do
    local detail = type_detail(item)
    if not detail and resolve then
      local resolved = request('completionItem/resolve', item)
      if resolved and resolved ~= vim.NIL then
        detail = type_detail(resolved)
      end
    end
    if detail then
      result[item.label] = detail
    end
  end
  return result
end

local function display(output)
  if not result_buffer or not api.nvim_buf_is_valid(result_buffer) then
    result_buffer = api.nvim_create_buf(false, true)
    api.nvim_buf_set_name(result_buffer, 'pyrefly-enhanced://result')
    vim.bo[result_buffer].buftype = 'nofile'
    vim.bo[result_buffer].bufhidden = 'hide'
    vim.bo[result_buffer].swapfile = false
    vim.bo[result_buffer].filetype = 'python'
  end
  local lines = vim.split(output, '\n', { plain = true })
  if lines[#lines] == '' then
    table.remove(lines)
  end
  vim.bo[result_buffer].modifiable = true
  api.nvim_buf_set_lines(result_buffer, 0, -1, false, lines)
  vim.bo[result_buffer].modified = false
  vim.bo[result_buffer].modifiable = false
  for _, tab in ipairs(api.nvim_list_tabpages()) do
    for _, win in ipairs(api.nvim_tabpage_list_wins(tab)) do
      if api.nvim_win_get_buf(win) == result_buffer then
        api.nvim_set_current_tabpage(tab)
        api.nvim_set_current_win(win)
        result_tab = tab
        return
      end
    end
  end
  if result_tab and api.nvim_tabpage_is_valid(result_tab) then
    api.nvim_set_current_tabpage(result_tab)
  else
    vim.cmd('tabnew')
    result_tab = api.nvim_get_current_tabpage()
  end
  api.nvim_win_set_buf(0, result_buffer)
end

local function project_root(bufnr)
  local best
  local path = api.nvim_buf_get_name(bufnr)
  for _, client in ipairs(vim.lsp.get_clients({ bufnr = bufnr })) do
    if pyrefly(client) then
      for _, root in ipairs(roots(client)) do
        if contains(root, path) and (not best or #root > #best) then
          best = root
        end
      end
    end
  end
  return best or vim.fn.getcwd()
end

function M.run(query)
  query = vim.trim(query or '')
  if query == '' then
    query = vim.fn.expand('<cword>')
  end
  if query == '' then
    query = vim.fn.input('Python class: ')
  end
  if query == '' then
    return
  end
  if not vim.system then
    error('pyrefly-enhanced requires Neovim 0.11 or newer')
  end
  local socket = vim.v.servername
  if socket == '' then
    socket = vim.fn.serverstart()
  end
  local root = project_root(api.nvim_get_current_buf())
  sequence = sequence + 1
  local current = sequence
  if process then
    process:kill(15)
    process = nil
  end
  local ok, job = pcall(vim.system, {
    options.python, plugin_root .. '/__main__.py', query,
    '--root', root, '--nvim', socket, '--timeout', tostring(options.timeout),
  }, { text = true, timeout = (options.timeout + 10) * 1000 }, function(result)
    vim.schedule(function()
      if current ~= sequence then
        return
      end
      process = nil
      if result.code ~= 0 then
        local message = vim.trim(result.stderr or '')
        vim.notify(message ~= '' and message or ('Python inspection exited with code ' .. result.code), vim.log.levels.ERROR)
        return
      end
      if result.stderr and vim.trim(result.stderr) ~= '' then
        vim.notify(vim.trim(result.stderr), vim.log.levels.WARN)
      end
      display(result.stdout or '')
    end)
  end)
  if not ok then
    vim.notify(tostring(job), vim.log.levels.ERROR)
    return
  end
  process = job
end

function M.setup(opts)
  opts = opts or {}
  if opts.python ~= nil and (type(opts.python) ~= 'string' or opts.python == '') then
    error('python must be an executable name or path')
  end
  if opts.timeout ~= nil and (type(opts.timeout) ~= 'number' or opts.timeout <= 0) then
    error('timeout must be a positive number of seconds')
  end
  options = vim.tbl_extend('force', options, opts)
end

return M
