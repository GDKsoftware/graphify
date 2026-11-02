unit Vis;

interface

type
  TVis = class
  strict private
    procedure Hidden;
  strict protected
    procedure Inner;
  published
    procedure Shown;
  end;

implementation

end.
