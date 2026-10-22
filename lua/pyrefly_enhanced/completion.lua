local M = {}

function M.complete(lead)
  local prefix, current = lead:match('^(.*)([+-][^+-]*)$')
  if not current then
    if lead ~= '' then return {} end
    prefix, current = '', ''
  end
  local stem, partial = current:match('^(.*:)([^:]*)$')
  local candidates = {}
  if stem then
    local category = stem:match('^[+-](%a+):')
    if category ~= 'method' and category ~= 'member' then return {} end
    local selected = {}
    for qualifier in stem:gmatch(':([%a]+)') do selected[qualifier] = true end
    for _, qualifier in ipairs({ 'pub', 'priv', 'self', 'cls' }) do
      local blocked = selected[qualifier]
        or ((qualifier == 'pub' or qualifier == 'priv') and (selected.pub or selected.priv))
        or ((qualifier == 'self' or qualifier == 'cls') and (selected.self or selected.cls))
      if not blocked and qualifier:sub(1, #partial) == partial then
        candidates[#candidates + 1] = prefix .. stem .. qualifier
      end
    end
  else
    for _, sign in ipairs({ '+', '-' }) do
      for _, category in ipairs({ 'all', 'method', 'member' }) do
        local candidate = sign .. category
        if candidate:sub(1, #current) == current then
          candidates[#candidates + 1] = prefix .. candidate
        end
      end
    end
  end
  return candidates
end

return M
