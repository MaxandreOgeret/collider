# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MOG Robotics OÜ

project('shared', 'c', version: '1.0.0')

inc_dir = include_directories('include')

lib_sources = files('simple_math.c')

simple_math_lib = library('simple_math',
  lib_sources,
  include_directories : inc_dir,
  dependencies: [dependency('libmd')],
  install : true,
)

simple_math_dep = declare_dependency(
  include_directories : inc_dir,
  link_with : simple_math_lib)
