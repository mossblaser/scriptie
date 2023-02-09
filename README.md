Scriptie
========

Scriptie is a little web application designed to provide a simple UI for
kicking off scripts.

![Screenshot of Scriptie](./doc/screenshots/screenshot.png)

Point Scriptie at a directory full of scripts and it will let you run them,
monitor them and kill them at the push of a button. By adding special comments,
Scriptie can also generate a simple form to enable arguments to be supplied.
High-level status and progress information can also be reported by printing
specially formatted messages too.


Why?
----

Sometimes the friction of SSH-ing into a machine and running a script is just
too high. Perhaps you can't be faffed using a fiddly terminal app on your
mobile to kick off a file download. Maybe you keep suspending your laptop
whilst running some file processing script on another machine and you forgot to
use 'screen' again so it got killed. Or you really wish someone less terminal
savvy could run the scripts too...

For many little tasks, building a fully-fledged UI to solve these problems
'properly' is just not worth it. This is why Scriptie exists. However small the
task, throw together a few lines of shell and drop in in a directory and you're
done!


Security
--------

Scriptie is pretty much one giant remote code execution exploit by design. Its
left up to you to put it behind suitably access control.


Quick-Start
-----------

The Scriptie server is started like so:

    $ scriptie /path/to/script/dir

All files with executable permissions in the top-level of the named directory
will be runnable via the Scriptie web interface served via
http://localhost:8080 (see `--help` for arguments to change the host and port)

By placing special comments in your script, as in the example below, Scriptie
can produce a simple UI for filling in arguments.

    #!/bin/bash
    
    ## name: Sleep Demo
    
    ## description:
    ##    This script will sleep for a number of seconds
    ##    and then exit.
    
    ## arg: int Seconds to sleep for
    
    ## arg: str Your name
    
    duration=${1}
    name=${2:-anonymous}
    
    if [ -z "$duration" ]; then
      echo "No duration specified!" >&2
      exit 1
    fi
    
    echo Hello, $name, time to sleep...
    sleep $duration
    echo Bye.

![Form produced for example script above](./doc/screenshots/sleep_demo_form.png)

Whilst Scriptie can show you the full output of any script as it runs (see
above), by printing special messages during execution Scriptie can show
at-a-glance progress. For example, if we replace the 'sleep' command above with
the following:

    echo "## status: Sleeping..."
    
    for i in `seq $duration`; do
      echo "## progress: $i/$duration"
      sleep 1
    done
    
    echo "## status: Done"

We now see live status information being displayed.

![Showing progress and status indication](./doc/screenshots/sleep_demo_with_status_running.png)


Reference
---------

The complete set of supported special comments and output lines are documented
below.

### Script metadata

The name of a script defaults to its name (with the extension removed) but can
be overridden by the 'name' directive anywhere on its own line in your script:

    ## name: Friendly name here

Optional descriptive text may also be provided which is shown on the
script-starting form.

    ## description:
    ##    You can write as long a description as you'd like here. It can wrap
    ##    over multiple lines like this.
    ##
    ##    Paragraphs (separated by a blank line) will be retained when
    ##    displayed.

For short descriptions, the whole declaration may be written on one line:

    ## description: Just a short description.


### Arguments

A (fixed) number of arguments may be defined using repeated 'arg' declarations
of the form:

    ## arg: type_name Optional label goes here

These may be located anywhere in your script but must appear in the order in
which you expect the arguments to your script.

The Scriptie UI will display a suitable HTML input entry for each argument and
will pass an argument per 'arg' declaration to your script. Be warned, however,
that Scriptie does not attempt to perform any input validation.

If you find yourself wanting something more fancy than a fixed set of
positional arguments you may wish to reconsider whether your script might
better have its own 'real' UI.

The following types are supported:

#### `bool`

![A bool argument in the UI](./doc/screenshots/arg_bool.png)

The `bool` type specifies a boolean argument and is displayed as a checkbox. It
will be passed to your script as the string 'true' or 'false'.

To set the default state of the argument, use 'bool:true' or 'bool:false'.


#### `number`, `float` and `int`

![A number argument in the UI](./doc/screenshots/arg_number.png)

The `number` type (and its alias `float`) specify a decimal numerical using a
HTML number input box.

The `int` type specifies a numerical input box with the step size set to '1'.

By default the inputs will be empty and if left empty will be passed as an
empty string to your script. A default value may be given using, e.g.,
`number:-123.4`.


#### `str`, `multi_line_str` and `password`

![A str argument in the UI](./doc/screenshots/arg_str.png)

The `str` type specifies a free-form single-line input box.

The `multi_line_str` type specifies a free-form multi-line text area.

The `password` type specifies a password field.

Default values may be given using, e.g., `str:hello`. Default values containing
spaces are not supported.


#### `file`

![A file argument in the UI](./doc/screenshots/arg_file.png)

The `file` type defaults to a file upload input. The uploaded file will be
saved to a temporary location on the server and the path to the uploaded file
passed as an argument to your script.

To restrict the file types shown in the file picker, you can include file
extensions and mime types using, e.g., `file:.jpg:.png:image/jpeg:image/png`.

Scriptie also provides an 'Upload' checkbox which, when unchecked, presents a
text box for entering a server-side filename directly instead. In this case,
the provided text will be passed to your script as-is and no files will be
uploaded.


#### `choice`

![A choice argument in the UI](./doc/screenshots/arg_choice.png)

The `choice` type produces a drop-down list of options with the selected option
being passed as the argument to your script.

The options in the list are given colon-separated after the type, e.g.
`choice:Foo:Bar:Baz`. Option values containing spaces are not supported.

The first option is used as the default value.


### Status and progress reports

Whilst Scriptie will display the full output produced by a script, scripts can
also print specially formatted status and progress reports which are displayed
conveniently in the UI.


#### Progress

![Examples of progress indicators](./doc/screenshots/progress.png)

Script progress may be reported by printing a message like the following:

    ## progress: 0.25

This is displayed in the Scriptie UI as a progress bar, in this example shown
25% to completion.

For scripts which perform a series of discrete tasks, a fraction may be printed
like so:

    ## progress: 3/7

Again the progress is indicated with a progress bar but the actual fraction is
also shown numerically. Fractional numerators are rounded down to a whole
number in the UI allowing for smoother progress bar updates within a discrete
step.


#### Status

![Example of status indication](./doc/screenshots/status.png)

Scripts may also report their current status using a message like the
following:

    ## status: Message here

The final status message reported by a script will be retained after it has
exited and can be a useful way of reporting on what happened (or why the script
failed, if exiting with a non-zero return code).


### Return code

Scripts which exit with a return code of 0 are reported as having succeeded
whilst those with a non-zero return code are treated as failed.

In the case of a negative exit code (indicating the script was ended by some
signal) the script is reported as having been killed.
