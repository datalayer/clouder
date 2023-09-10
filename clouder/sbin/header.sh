# Copyright (c) Datalayer, Inc. https://datalayer.io
# Distributed under the terms of the MIT License.

if [ "$DATALAYER_SHOW_HEADER" == "false" ]
then
  exit 0
fi

if [ "$DATALAYER_SKIP_HEADER" == "true" ]
then
  exit 0
fi

echo $GREEN$BOLD"""   ___ _    ___  _   _ ___  ___ ___ 
  / __| |  / _ \| | | |   \| __| _ \\
 | (__| |_| (_) | |_| | |) | _||   /
  \___|____\___/ \___/|___/|___|_|_\\
                                    
Copyright (c) Datalayer, Inc. https://datalayer.io"""
echo $NOBOLD$NOCOLOR
