#!/usr/bin/env bash

# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

if [ "$CLOUDER_SHOW_HEADER" == "false" ]
then
  exit 0
fi

if [ "$CLOUDER_SKIP_HEADER" == "true" ]
then
  exit 0
fi

echo -e $GREEN$BOLD"""┏┓┓     ┓    
┃ ┃┏┓┓┏┏┫┏┓┏┓
┗┛┗┗┛┗┻┗┻┗ ┛ 

Copyright (c) Datalayer, Inc. https://datalayer.io"""
echo -e $NOBOLD$NOCOLOR
