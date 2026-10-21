if vim.g.loaded_pyrefly_enhanced then
  return
end
vim.g.loaded_pyrefly_enhanced = true

vim.api.nvim_create_user_command('PyreflyEnhanced', function(args)
  require('pyrefly_enhanced').run(args.args)
end, {
  nargs = '?',
  desc = 'Inspect a Python class using the existing pyrefly language server',
})
