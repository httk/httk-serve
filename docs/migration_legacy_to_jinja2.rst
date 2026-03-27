Migration: Legacy Templates to Jinja2
=====================================

This guide maps common legacy template patterns to their modern Jinja2 equivalents.

Overview
--------

Legacy projects typically use ``.httkweb.html`` templates with formatter-style
constructs such as ``{name:repeat::...}``, ``{func:call:...}``, and conditional
specifiers. Modern projects should use ``.html.j2`` (or ``.jinja``/``.j2``)
with standard Jinja2 syntax.

Template file naming
--------------------

Legacy
^^^^^^

- ``default.httkweb.html``
- ``base_default.httkweb.html``
- function fragments like ``search_result.httkweb.html``

Modern
^^^^^^

- ``default.html.j2``
- ``base_default.html.j2``
- function fragments like ``search_result.html.j2``

Variable access
---------------

Legacy:

.. code-block:: text

   {title}
   {page.relurl}

Modern:

.. code-block:: jinja

   {{ title }}
   {{ page.relurl }}

Escaping behavior
-----------------

Legacy templates often rely on formatter-level quote/unquoted controls.
In Jinja2, use autoescaping defaults and explicit ``|safe`` only when needed.

Legacy:

.. code-block:: text

   {content}
   {content:unquoted}

Modern:

.. code-block:: jinja

   {{ content }}
   {{ content|safe }}

Loops
-----

Legacy ``repeat``:

.. code-block:: text

   {menuitems:repeat::
     <li>{{item}}</li>
   }

Modern Jinja2:

.. code-block:: jinja

   {% for item in menuitems %}
   <li>{{ item }}</li>
   {% endfor %}

Conditionals
------------

Legacy conditionals are encoded in format specs (for example ``if`` / ``if-not``).
Use Jinja2 control blocks directly.

Legacy:

.. code-block:: text

   {error_404_reason:if:Reason: {error_404_reason}}

Modern:

.. code-block:: jinja

   {% if error_404_reason %}
   Reason: {{ error_404_reason }}
   {% endif %}

Function-style calls
--------------------

Legacy ``call`` often appears around helper access:

.. code-block:: text

   {{pages:call:{{item}}:title}}

Modern:

.. code-block:: jinja

   {{ pages(item, 'title') }}

Indexing and attribute access
-----------------------------

Legacy:

.. code-block:: text

   {{item[id]}}
   {{item[formula]}}

Modern:

.. code-block:: jinja

   {{ item['id'] }}
   {{ item['formula'] }}

Function fragments (metadata)
-----------------------------

Legacy metadata often points to fragment names without modern suffixes.
Modern projects should use clear names and Jinja templates.

Legacy metadata:

.. code-block:: yaml

   results-function: search:q:search_result

Modern metadata (same key format, modern template files):

.. code-block:: yaml

   results-function: search:q:search_result

and template file:

.. code-block:: text

   templates/search_result.html.j2

Metadata key casing and structure
---------------------------------

Prefer lowercase keys in modern projects:

- ``template`` instead of ``Template``
- ``base_template`` instead of ``Base_template``
- ``title`` instead of ``Title``

Both styles may work in compatibility contexts, but lowercase metadata avoids
surprises and keeps templates predictable.

Migration checklist
-------------------

1. Rename templates to ``.html.j2``.
2. Replace legacy formatter expressions with Jinja2 syntax.
3. Keep helper usage explicit: ``pages(...)``, ``first_value(...)``, ``listdir(...)``.
4. Normalize metadata keys to lowercase.
5. Run your site with ``compatibility_mode=False`` and verify output.

Tip
---

If needed, migrate page-by-page:

- Keep legacy project operational in compatibility mode.
- Port one template at a time to Jinja2.
- Switch the site to current mode once templates and metadata are fully modernized.
