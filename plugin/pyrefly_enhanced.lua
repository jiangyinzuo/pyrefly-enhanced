if vim.g.loaded_pyrefly_enhanced then
  return
end
vim.g.loaded_pyrefly_enhanced = true

vim.api.nvim_create_user_command('PyreflyEnhanced', function(args)
  require('pyrefly_enhanced').run(args.args)
end, {
  nargs = '*',
  complete = function(lead)
    return require('pyrefly_enhanced.completion').complete(lead)
  end,
  desc = 'Inspect Python classes using the existing pyrefly language server',
})
